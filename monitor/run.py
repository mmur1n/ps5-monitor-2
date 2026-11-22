"""One unattended scan. Secrets are read only from the environment."""
import argparse
import copy
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .source import SOURCE_URL, SourceError, scan


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def reconcile(previous, observations, timestamp):
    state = copy.deepcopy(previous)
    old = {p["id"]: p for p in state.get("products", [])}
    products, events = [], state.get("events", [])
    for item in observations:
        before = old.pop(item["id"], {})
        last = before.get("lastKnownStatus")
        item = {**item, "checkedAt": timestamp, "listed": True,
                "lastKnownStatus": last, "lastSuccessAt": before.get("lastSuccessAt")}
        if item["status"] != "unknown":
            item["lastKnownStatus"] = item["status"]
            item["lastSuccessAt"] = timestamp
        changed = item["status"] != before.get("status")
        item["changedAt"] = timestamp if changed else before.get("changedAt", timestamp)
        if changed:
            events.append({"id": f'{item["id"]}:{timestamp}', "productId": item["id"],
                           "name": item["name"], "status": item["status"], "at": timestamp,
                           "notify": item["status"] == "in_stock" and last != "in_stock",
                           "sentAt": None})
        products.append(item)
    # A listing disappearing is not evidence of stock becoming available/unavailable.
    for item in old.values():
        products.append({**item, "listed": False, "status": "removed"})
    state.update({"products": products, "events": events[-120:]})
    return state


def send_telegram(token, chat_id, message):
    """Never include URLs, Telegram responses or exceptions in logs: they may contain secrets."""
    if not re.fullmatch(r"\d+:[A-Za-z0-9_-]+", token) or not re.fullmatch(r"-?\d+", chat_id):
        return False
    body = json.dumps({"chat_id": chat_id, "text": message,
                       "link_preview_options": {"is_disabled": True}}).encode()
    try:
        with urlopen(Request(f"https://api.telegram.org/bot{token}/sendMessage", data=body,
                             headers={"Content-Type": "application/json"}), timeout=20) as response:
            return json.load(response).get("ok") is True
    except (HTTPError, URLError, OSError, ValueError):
        return False


def notify(state, token, chat_id, send=send_telegram, test=False):
    configured = bool(token and chat_id)
    telegram = state.setdefault("telegram", {})
    telegram.update({"configured": configured, "error": None})
    if not configured:
        return
    panel = "https://mmur1n.github.io/ps5-monitor-2/"
    if test or not telegram.get("welcomeSentAt"):
        message = ("🎮 Мониторинг дисковых PS5 подключён.\n"
                   "Проверка каждые 5 минут (возможны задержки GitHub).\n"
                   "Digital и PS5 Pro без дисковода исключены.\n"
                   "Уведомлю, когда консоль появится в наличии и станет доступна покупка.\n\n" + panel)
        if send(token, chat_id, message):
            telegram["welcomeSentAt"] = now()
            telegram["lastSentAt"] = now()
        else:
            telegram["error"] = "Не удалось отправить сообщение. Проверь Secrets и /start у бота."
            return
    products = {p["id"]: p for p in state.get("products", [])}
    for event in state.get("events", []):
        if not event.get("notify") or event.get("sentAt") or event.get("cancelled"):
            continue
        product = products.get(event["productId"])
        if not product or product["status"] not in ("in_stock", "unknown"):
            event["cancelled"] = True
            continue
        if product["status"] == "unknown" or state.get("health") == "error":
            continue
        message = (f'🟢 PS5 с дисководом доступна!\n\n{product["name"]}\n'
                   f'{product.get("price") or "Цена на сайте"}\n\n{product["url"]}\n\n'
                   f'Панель: {panel}')
        if send(token, chat_id, message):
            event["sentAt"] = now()
            telegram["lastSentAt"] = now()
        else:
            telegram["error"] = "Ошибка Telegram. Неотправленное уведомление будет повторено."
            break


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, default=Path("state/state.json"))
    parser.add_argument("--public", type=Path, default=Path("web/status.json"))
    parser.add_argument("--test-telegram", action="store_true")
    parser.add_argument("--no-telegram", action="store_true")
    args = parser.parse_args()
    # Corrupt state is a hard failure: silently starting again could duplicate alerts.
    state = json.loads(args.state.read_text()) if args.state.exists() else {}
    state.setdefault("products", [])
    state.setdefault("events", [])
    timestamp = now()
    state.update({"version": 1, "source": SOURCE_URL, "intervalMinutes": 5, "attemptedAt": timestamp})
    try:
        products, total = scan()
        state = reconcile(state, products, timestamp)
        failures = sum(p["status"] == "unknown" for p in products)
        state.update({"catalogueTotal": total, "health": "partial" if failures else "ok",
                      "error": f"Не проверено товаров: {failures}" if failures else None})
        if not failures:
            state["lastSuccessAt"] = timestamp
    except (SourceError, ValueError, KeyError, TypeError):
        state.update({"health": "error", "error": "PlayStation не ответил или изменил формат данных. Предыдущие статусы сохранены."})
    if not args.no_telegram:
        notify(state, os.environ.get("TELEGRAM_BOT_TOKEN", ""),
               os.environ.get("TELEGRAM_CHAT_ID", ""), test=args.test_telegram)
    else:
        state.setdefault("telegram", {"configured": False})
    save(args.state, state)
    # State contains product information only, never a token, chat ID or Telegram response.
    save(args.public, state)
    print(f'Проверка завершена: {state["health"]}; товаров: {len(state.get("products", []))}; '
          f'Telegram: {"подключён" if state.get("telegram", {}).get("configured") else "не настроен"}')
    return 1 if state["health"] != "ok" or state.get("telegram", {}).get("error") else 0


if __name__ == "__main__":
    raise SystemExit(main())
