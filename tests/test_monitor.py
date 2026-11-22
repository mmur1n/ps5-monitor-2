import json
import unittest
from urllib.parse import parse_qs, urlparse

from monitor.run import notify, reconcile
from monitor.source import SourceError, catalogue, is_disc_console, parse_product

PID = 'PSD001-PSDE00000_00-PS5CONSOLE'


def page(raw, ctas=None, product_id=PID):
    payload = {'args': {'productId': product_id}, 'cache': {
        str(i): {'__typename': 'GameCTA', **cta} for i, cta in enumerate(ctas or [])}}
    flight = 'x:' + json.dumps({'_raw': raw}) + '\n' + '<script type="application/json">' + json.dumps(payload) + '</script>'
    return '<script>self.__next_f.push(' + json.dumps([1, flight]) + ')</script>'


def raw(stock='IN_STOCK', override='notSpecified'):
    return {'id': PID, 'name': 'PlayStation®5 Konsole', 'topCategory': 'CONSOLE',
            'skus': [{'id': PID + '-E0000', 'stockLevelStatus': stock,
                      'physicalAttributes': {'inventoryOverride': override}}]}


def observation(status):
    return {'id': PID, 'status': status, 'name': 'PS5', 'url': 'https://www.playstation.com/', 'price': '€549,99'}


class SourceTests(unittest.TestCase):
    def test_disc_filter(self):
        for name in ['PlayStation®5 Konsole', 'Paket: PlayStation®5 Konsole 1 TB', 'Generalüberholt und Zertifiziert PlayStation®5 Konsole']:
            self.assertTrue(is_disc_console({'name': name}))
        for name in ['PlayStation®5 Digital Edition Konsole', 'PlayStation®5 Pro Console', 'Disc-Laufwerk für PS5', 'PS5 Konsole ohne Laufwerk']:
            self.assertFalse(is_disc_console({'name': name}))

    def test_available_requires_positive_cta_and_stock(self):
        self.assertEqual(parse_product(page(raw(), [{'type': 'ADD_TO_CART'}]), PID)['status'], 'in_stock')

    def test_inventory_alone_does_not_trigger_alert(self):
        self.assertEqual(parse_product(page(raw()), PID)['status'], 'unknown')

    def test_contradictory_cta_is_shown_separately(self):
        self.assertEqual(parse_product(page(raw(), [{'type': 'UNAVAILABLE'}]), PID)['status'], 'blocked')

    def test_out_of_stock_wins_over_cta(self):
        self.assertEqual(parse_product(page(raw('OUT_OF_STOCK'), [{'type': 'ADD_TO_CART'}]), PID)['status'], 'out_of_stock')

    def test_inventory_override_wins(self):
        self.assertEqual(parse_product(page(raw(override='forceOutOfStock'), [{'type': 'ADD_TO_CART'}]), PID)['status'], 'out_of_stock')

    def test_other_product_cta_is_ignored(self):
        self.assertEqual(parse_product(page(raw(), [{'type': 'ADD_TO_CART'}], product_id='ANOTHER'), PID)['status'], 'unknown')

    def test_other_sku_cta_is_ignored(self):
        cta={'type':'ADD_TO_CART','action':{'param':[{'name':'skuId','value':'ANOTHER-SKU'}]}}
        self.assertEqual(parse_product(page(raw(), [cta]), PID)['status'], 'unknown')

    def test_invalid_page_fails_closed(self):
        with self.assertRaises(SourceError): parse_product('<h1>Access denied</h1>', PID)

    def test_all_pages_are_loaded(self):
        calls=[]
        def get(url, **kwargs):
            if 'graphql' not in url:
                return '<script>self.__next_f.push('+json.dumps([1,'x:{"category":"813f2882-2e7e-43a3-9318-5d238e6dfe2a"}'])+')</script>'
            offset=json.loads(parse_qs(urlparse(url).query)['variables'][0])['pageArgs']['offset']; calls.append(offset)
            return json.dumps({'data':{'categoryGridRetrieve':{'products':[{'id':str(offset),'name':'PlayStation®5 Konsole'}], 'pageInfo':{'totalCount':2,'isLast':offset==1}}}})
        products,total=catalogue(get)
        self.assertEqual(calls,[0,1]); self.assertEqual(total,2); self.assertEqual(len(products),2)


class NotificationTests(unittest.TestCase):
    def test_unknown_does_not_create_a_second_restock(self):
        state=reconcile({},[observation('in_stock')],'t1')
        state=reconcile(state,[observation('unknown')],'t2')
        state=reconcile(state,[observation('in_stock')],'t3')
        self.assertEqual(sum(e['notify'] for e in state['events']),1)

    def test_new_restock_creates_new_alert(self):
        state={}
        for time,status in enumerate(['out_of_stock','in_stock','in_stock','out_of_stock','in_stock']):
            state=reconcile(state,[observation(status)],str(time))
        self.assertEqual(sum(e['notify'] for e in state['events']),2)

    def test_successful_alert_is_not_repeated(self):
        state=reconcile({},[observation('in_stock')],'t1'); sent=[]
        sender=lambda *args: sent.append(args) or True
        notify(state,'token','chat',send=sender);notify(state,'token','chat',send=sender)
        self.assertEqual(len(sent),2)  # welcome and one stock alert
        self.assertNotIn('token',json.dumps(state));self.assertNotIn('"chat"',json.dumps(state))

    def test_failed_send_is_retried(self):
        state=reconcile({},[observation('in_stock')],'t1')
        state['telegram']={'welcomeSentAt':'t0'}
        notify(state,'token','chat',send=lambda *a:False)
        self.assertIsNone(state['events'][0]['sentAt'])
        notify(state,'token','chat',send=lambda *a:True)
        self.assertIsNotNone(state['events'][0]['sentAt'])

    def test_stale_pending_alert_is_cancelled(self):
        state=reconcile({},[observation('in_stock')],'t1')
        state=reconcile(state,[observation('out_of_stock')],'t2')
        state['telegram']={'welcomeSentAt':'t0'}
        sent=[];notify(state,'token','chat',send=lambda *a:sent.append(a) or True)
        self.assertFalse(sent)

    def test_source_failure_does_not_send_old_stock(self):
        state=reconcile({},[observation('in_stock')],'t1');state['health']='error';state['telegram']={'welcomeSentAt':'t0'}
        sent=[];notify(state,'token','chat',send=lambda *a:sent.append(a) or True)
        self.assertFalse(sent)


if __name__ == '__main__': unittest.main()
