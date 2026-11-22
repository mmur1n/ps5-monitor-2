"""Read the public catalogue and the selected product's purchase CTA, without login."""
import gzip
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from html import unescape
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

SOURCE_URL = "https://www.playstation.com/de-de/consoles/ps5/shop-all/"
GRAPHQL_URL = "https://web.np.playstation.com/api/graphql/v1/op"
LIST_HASH = "985dd5779aa78ce64beb987653b44cf3b5b9a23bae605f759c82b44bd4df4c91"


class SourceError(Exception):
    pass


def fetch(url, *, data=None, headers=None):
    common = {"User-Agent": "Mozilla/5.0 (compatible; PS5StockMonitor/1.0)",
              "Accept-Encoding": "gzip", "Accept-Language": "de-DE,de;q=0.9"}
    common.update(headers or {})
    for attempt in range(3):
        try:
            with urlopen(Request(url, data=data, headers=common), timeout=25) as response:
                body = response.read(8_000_001)
                if len(body) > 8_000_000:
                    raise SourceError("Ответ источника слишком большой")
                if response.headers.get("Content-Encoding") == "gzip" or body[:2] == b"\x1f\x8b":
                    body = gzip.decompress(body)
                return body.decode("utf-8")
        except HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise SourceError(f"Источник вернул HTTP {error.code}") from None
        except (URLError, TimeoutError, OSError):
            if attempt == 2:
                raise SourceError("Источник не ответил") from None
        time.sleep(2 ** attempt)


def flight_text(html):
    pieces = []
    for match in re.finditer(r"self\.__next_f\.push\((.*?)\)</script>", html, re.S):
        try:
            item = json.loads(match[1])
            if len(item) > 1 and isinstance(item[1], str):
                pieces.append(item[1])
        except (ValueError, TypeError):
            continue
    if not pieces:
        raise SourceError("Изменился формат страницы PlayStation")
    return "".join(pieces)


def json_values_after(text, key):
    decoder = json.JSONDecoder()
    for match in re.finditer(re.escape(json.dumps(key)) + r"\s*:\s*", text):
        try:
            yield decoder.raw_decode(text[match.end():])[0]
        except ValueError:
            continue


def is_disc_console(product):
    name = product.get("name", "").lower()
    return (bool(re.search(r"(?:playstation\s*[®™]?\s*5|\bps5\b)", name))
            and bool(re.search(r"konsole|console", name))
            and not re.search(r"digital|\bpro\b|ohne\s+(?:disc|laufwerk)", name)
            and product.get("topCategory", "CONSOLE") == "CONSOLE")


def clean_name(name):
    return re.sub(r"\s+", " ", name.replace("\\t", " ")).strip().rstrip("*").strip()


def catalogue(get=fetch):
    html = get(SOURCE_URL)
    text = flight_text(html)
    category = next((v for v in json_values_after(text, "category")
                     if isinstance(v, str) and re.fullmatch(r"[\da-f-]{36}", v)), None)
    if not category:
        raise SourceError("Не найден каталог консолей")
    products, offset, query_hash = {}, 0, LIST_HASH
    for page in range(20):
        variables = {"id": category, "pageArgs": {"size": 100, "offset": offset},
                     "sortBy": None, "filterBy": [], "facetOptions": [], "maxResults": 2000}
        def request_page():
            query = {"operationName": "getWraithProductList", "variables": json.dumps(variables),
                     "extensions": json.dumps({"persistedQuery": {"version": 1, "sha256Hash": query_hash}})}
            return json.loads(get(GRAPHQL_URL + "?" + urlencode(query), headers={
                "Content-Type": "application/json", "x-psn-store-locale-override": "de-DE",
                "apollographql-client-name": "wraith"}))
        result = request_page()
        if result.get("errors") and page == 0:
            # Persisted-query hashes change when Sony deploys its frontend.
            for path in dict.fromkeys(re.findall(r'<script[^>]*src="(/_next/[^"<>]+\.js)"', html)):
                script = get("https://www.playstation.com" + path)
                match = re.search(r'getWraithProductList\s*:\s*"([\da-f]{64})"', script)
                if match:
                    query_hash = match[1]
                    break
            result = request_page()
        grid = (result.get("data") or {}).get("categoryGridRetrieve")
        if result.get("errors") or not grid or not isinstance(grid.get("products"), list):
            raise SourceError("Не удалось прочитать полный каталог PlayStation")
        batch, info = grid["products"], grid.get("pageInfo", {})
        products.update({p["id"]: p for p in batch})
        if info.get("isLast") is True or len(products) >= info.get("totalCount", 10**9):
            if not products:
                raise SourceError("Каталог неожиданно пуст")
            selected = [p for p in products.values() if is_disc_console(p)]
            if not selected:
                raise SourceError("В каталоге не найдены дисковые консоли")
            return selected, len(products)
        if not batch:
            break
        offset += len(batch)
    raise SourceError("Получен неполный каталог PlayStation")


def parse_product(html, product_id):
    text = flight_text(html)
    raw = next((v for v in json_values_after(text, "_raw")
                if isinstance(v, dict) and v.get("id") == product_id), None)
    if not raw or not is_disc_console(raw):
        raise SourceError("Не подтверждена карточка дисковой консоли")
    ctas = []
    for match in re.finditer(r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', text, re.S):
        try:
            payload = json.loads(unescape(match[1]))
        except ValueError:
            continue
        if payload.get("args", {}).get("productId") != product_id:
            continue
        for value in payload.get("cache", {}).values():
            if isinstance(value, dict) and value.get("__typename") == "GameCTA":
                params = value.get("action", {}).get("param", [])
                if any(p.get("name") == "skuId" and not p.get("value", "").startswith(product_id + "-") for p in params):
                    continue
                ctas.append(value)
    skus = [s for s in raw.get("skus", []) if s.get("id", "").startswith(product_id + "-")]
    if not skus:
        raise SourceError("Нет данных о наличии выбранного товара")
    inventory = {s.get("stockLevelStatus") for s in skus}
    overrides = {s.get("physicalAttributes", {}).get("inventoryOverride") for s in skus}
    types = {c.get("type") for c in ctas}
    actions = {c.get("action", {}).get("type") for c in ctas}
    hidden = any(c.get("local", {}).get("hideCta") for c in ctas)
    stock = "in_stock" if "IN_STOCK" in inventory else "out_of_stock" if inventory == {"OUT_OF_STOCK"} else "unknown"
    if "forceOutOfStock" in overrides:
        stock = "out_of_stock"
    can_buy = bool(types & {"ADD_TO_CART", "BUY_NOW", "PURCHASE"}
                   or actions & {"ADD_TO_CART", "BUY_NOW"})
    if types & {"UNAVAILABLE", "NOT_AVAILABLE"} or hidden:
        status = "blocked" if stock == "in_stock" else "out_of_stock"
        reason = "Отметка наличия есть, но покупка недоступна" if status == "blocked" else "Покупка недоступна"
    elif types & {"PRE_ORDER", "PREORDER"}:
        status, reason = "preorder", "Доступен предзаказ"
    elif can_buy and stock == "out_of_stock":
        status, reason = "stock_conflict", "Сайт разрешает покупку при отметке «нет в наличии». Успех заказа не подтверждён."
    elif can_buy and stock == "in_stock":
        status, reason = "in_stock", "Есть запас и доступна покупка"
    elif stock == "out_of_stock":
        status, reason = "out_of_stock", "Нет в наличии у PlayStation"
    else:
        status, reason = "unknown", "Не удалось подтвердить возможность покупки"
    return {"status": status, "reason": reason, "inventory": stock,
            "ctaTypes": sorted(t for t in types if t),
            "ctaActions": sorted(a for a in actions if a),
            "price": next((s.get("displayPrice") for s in skus if s.get("displayPrice")), None)}


def check_product(product, get=fetch):
    url = "https://www.playstation.com/api/pdp-redirect/?" + urlencode({"id": product["id"], "locale": "de-de"})
    media = next((m.get("url") for m in product.get("media", []) if m.get("role") == "MASTER"), None)
    base = {"id": product["id"], "name": clean_name(product["name"]), "url": url,
            "image": media, "refurbished": "generalüberholt" in product["name"].lower(),
            "price": (product.get("price") or {}).get("discountedPrice")}
    try:
        details = parse_product(get(url), product["id"])
        if not details.get("price"):
            details.pop("price", None)
        return {**base, **details, "error": None}
    except (SourceError, ValueError, KeyError, TypeError):
        return {**base, "status": "unknown", "inventory": "unknown",
                "reason": "Не удалось проверить карточку", "error": "Ошибка чтения товара"}


def scan(get=fetch):
    products, total = catalogue(get)
    with ThreadPoolExecutor(max_workers=3) as pool:
        checked = list(pool.map(lambda p: check_product(p, get), products))
    return checked, total
