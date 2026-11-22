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

    def test_buyable_out_of_stock_is_a_stock_conflict(self):
        self.assertEqual(parse_product(page(raw('OUT_OF_STOCK'), [{'type': 'ADD_TO_CART'}]), PID)['status'], 'stock_conflict')

    def test_forced_out_of_stock_with_buy_cta_is_a_conflict(self):
        result=parse_product(page(raw(override='forceOutOfStock'), [{'type': 'ADD_TO_CART'}]), PID)
        self.assertEqual(result['status'], 'stock_conflict')
        self.assertEqual(result['inventory'], 'out_of_stock')

    def test_out_of_stock_without_buy_cta_is_not_a_conflict(self):
        self.assertEqual(parse_product(page(raw('OUT_OF_STOCK')), PID)['status'], 'out_of_stock')

    def test_buy_action_is_detected_when_inventory_is_empty(self):
        cta={'type':'OTHER','action':{'type':'BUY_NOW'}}
        self.assertEqual(parse_product(page(raw('OUT_OF_STOCK'), [cta]), PID)['status'], 'stock_conflict')

    def test_hidden_buy_button_does_not_trigger_conflict(self):
        cta={'type':'ADD_TO_CART','local':{'hideCta':True}}
        self.assertEqual(parse_product(page(raw('OUT_OF_STOCK'), [cta]), PID)['status'], 'out_of_stock')

    def test_unavailable_cta_wins_over_conflicting_buy_cta(self):
        ctas=[{'type':'UNAVAILABLE'},{'type':'ADD_TO_CART'}]
        self.assertEqual(parse_product(page(raw('OUT_OF_STOCK'), ctas), PID)['status'], 'out_of_stock')

    def test_unknown_inventory_is_not_reported_as_empty(self):
        self.assertEqual(parse_product(page(raw('UNKNOWN'), [{'type':'ADD_TO_CART'}]), PID)['status'], 'unknown')

    def test_preorder_is_not_reported_as_a_buyable_stock_conflict(self):
        cta={'type':'PRE_ORDER','action':{'type':'ADD_TO_CART'}}
        self.assertEqual(parse_product(page(raw('OUT_OF_STOCK'), [cta]), PID)['status'], 'preorder')

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
    def test_stock_conflict_sends_one_warning_and_link(self):
        details=parse_product(page(raw('OUT_OF_STOCK'),[{'type':'ADD_TO_CART'}]),PID)
        state=reconcile({},[{**observation('stock_conflict'),**details}],'t1')
        state['telegram']={'welcomeSentAt':'t0'}
        sent=[];sender=lambda *a:sent.append(a[2]) or True
        notify(state,'token','chat',send=sender)
        state=reconcile(state,[observation('stock_conflict')],'t2')
        notify(state,'token','chat',send=sender)
        self.assertEqual(len(sent),1)
        self.assertIn('⚠️',sent[0]);self.assertIn('нет в наличии',sent[0])
        self.assertIn('https://www.playstation.com/',sent[0])
        self.assertIn('успех оформления заказа не проверялся',sent[0])

    def test_stock_conflict_recovery_after_unknown_does_not_repeat_alert(self):
        state={}
        for time,status in enumerate(['out_of_stock','stock_conflict','unknown','stock_conflict']):
            state=reconcile(state,[observation(status)],str(time))
        self.assertEqual(sum(e['notify'] for e in state['events']),1)

    def test_restored_stock_after_conflict_sends_a_new_confirmed_alert(self):
        state=reconcile({},[observation('stock_conflict')],'t1')
        state=reconcile(state,[observation('in_stock')],'t2')
        state['telegram']={'welcomeSentAt':'t0'}
        sent=[];notify(state,'token','chat',send=lambda *a:sent.append(a[2]) or True)
        self.assertEqual(len(sent),1);self.assertIn('🟢',sent[0])
        self.assertTrue(state['events'][0]['cancelled'])

    def test_stock_conflict_send_failure_is_retried(self):
        state=reconcile({},[observation('stock_conflict')],'t1');state['telegram']={'welcomeSentAt':'t0'}
        notify(state,'token','chat',send=lambda *a:False)
        self.assertIsNone(state['events'][0]['sentAt'])
        notify(state,'token','chat',send=lambda *a:True)
        self.assertIsNotNone(state['events'][0]['sentAt'])

    def test_new_conflict_after_purchase_is_disabled_sends_new_alert(self):
        state={}
        for time,status in enumerate(['stock_conflict','stock_conflict','out_of_stock','stock_conflict']):
            state=reconcile(state,[observation(status)],str(time))
        self.assertEqual(sum(e['notify'] for e in state['events']),2)

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
