import json
import unittest

from web import web as web_module


class FakePypilotClient:
    def __init__(self, values=None, connect=True):
        self.connection = False
        self.connect = connect
        self.values = values or {}
        self.sent = []
        self.requested = []
        self.disconnected = False

    def poll(self, timeout=0):
        if self.connect:
            self.connection = True

    def send(self, message):
        self.sent.append(message)

    def watch(self, name, value=True):
        self.requested.append(name)

    def receive(self):
        values = {}
        while self.requested:
            name = self.requested.pop(0)
            if name in self.values:
                values[name] = self.values[name]
        return values

    def disconnect(self):
        self.disconnected = True


class WebApiTests(unittest.TestCase):
    def setUp(self):
        self.created_clients = []
        web_module.app.config['TESTING'] = True
        web_module.app.config['PYPILOT_CLIENT_FACTORY'] = self.make_client
        self.app = web_module.app.test_client()

    def tearDown(self):
        web_module.app.config.pop('PYPILOT_CLIENT_FACTORY', None)

    def make_client(self):
        client = FakePypilotClient(values={
            'ap.enabled': True,
            'profile': {'name': 'cruise'},
        })
        self.created_clients.append(client)
        return client

    def test_api_can_set_and_get_values(self):
        response = self.app.post('/api/pypilot', json={
            'set': {
                'ap.enabled': True,
                'profile': {'name': 'cruise'},
            },
            'get': ['ap.enabled', 'profile'],
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {
            'ok': True,
            'values': {
                'ap.enabled': True,
                'profile': {'name': 'cruise'},
            },
        })

        client = self.created_clients[0]
        sent = {}
        for message in client.sent:
            name, value = message.rstrip().split('=', 1)
            sent[name] = json.loads(value)
        self.assertEqual(sent, {
            'ap.enabled': True,
            'profile': {'name': 'cruise'},
        })
        self.assertTrue(client.disconnected)

    def test_api_rejects_invalid_payload(self):
        response = self.app.post('/api/pypilot', json={'set': 'ap.enabled'})

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()['ok'])

    def test_api_reports_connection_failure(self):
        def disconnected_client():
            client = FakePypilotClient(connect=False)
            self.created_clients.append(client)
            return client

        web_module.app.config['PYPILOT_CLIENT_FACTORY'] = disconnected_client

        response = self.app.post('/api/pypilot', json={'get': ['ap.enabled']})

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json(), {
            'ok': False,
            'message': 'Unable to connect to pypilot.',
        })
        self.assertTrue(self.created_clients[-1].disconnected)


if __name__ == '__main__':
    unittest.main()
