#!/usr/bin/env python
#   Copyright (C) 2021 Sean D'Epagnier
#
# This Program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public
# License as published by the Free Software Foundation; either
# version 3 of the License, or (at your option) any later version.  

import sys, os, json, time
from pathlib import Path
from flask import Flask, render_template, session, request, Markup, jsonify, send_file

from flask_socketio import SocketIO, Namespace, emit, join_room, leave_room, \
    close_room, rooms, disconnect

from engineio.payload import Payload
Payload.max_decode_packets = 500

from pypilot.client import pypilotClient
from pypilot import pyjson

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import tinypilot

config = {'port': 8000, 'language': 'default'}
configfilename = os.getenv('HOME')+'/.pypilot/web.conf'

try:
    file = open(configfilename, 'r')
    config.update(pyjson.loads(file.readline()))
    file.close()

except:
    print('failed to read config', configfilename)

def write_config():
    try:
        file = open(configfilename, 'w')
        file.write(pyjson.dumps(config) + '\n')
        file.close()
    except:
        print('failed to write config')

if len(sys.argv) > 1:
    pypilot_web_port=int(sys.argv[1])
else:
    pypilot_web_port = config['port']

print('using port', pypilot_web_port)
        

# Set this variable to 'threading', 'eventlet' or 'gevent' to test the
# different async modes, or leave it set to None for the application to choose
# the best option based on installed packages.
async_mode = None

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app, async_mode=async_mode, cors_allowed_origins="*")


def _telemetry_root():
    return Path(os.getenv('PYPILOT_RECORD_ROOT', os.path.join(os.getenv('HOME'), '.pypilot', 'recordings')))


def _read_json(path):
    try:
        with path.open('r', encoding='utf-8') as handle:
            value = json.load(handle)
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def _session_dirs(root):
    if not root.exists() or not root.is_dir():
        return {}
    result = {}
    for child in root.iterdir():
        if child.is_dir():
            result[child.name] = child
    return result


def _telemetry_artifacts(session_dir):
    file_map = {
        'telemetry': 'telemetry.jsonl',
        'telemetryMeta': 'telemetry_meta.json',
        'telemetrySummary': 'telemetry_summary.json',
    }
    artifacts = {'telemetry': False, 'telemetryMeta': False, 'telemetrySummary': False,
                 'video': False, 'videoMeta': False, 'videoSummary': False}
    paths = {'telemetry': None, 'telemetryMeta': None, 'telemetrySummary': None,
             'video': None, 'videoMeta': None, 'videoSummary': None}
    if not session_dir:
        return artifacts, paths
    for name, filename in file_map.items():
        candidate = session_dir / filename
        if candidate.exists():
            artifacts[name] = True
            paths[name] = str(candidate)
    return artifacts, paths


def _telemetry_artifact_path(session_id, artifact_key):
    file_map = {
        'telemetry': 'telemetry.jsonl',
        'telemetryMeta': 'telemetry_meta.json',
        'telemetrySummary': 'telemetry_summary.json',
    }
    filename = file_map.get(artifact_key)
    if not filename:
        return None
    session_dir = _session_dirs(_telemetry_root()).get(session_id)
    if not session_dir:
        return None
    candidate = session_dir / filename
    if not candidate.exists() or not candidate.is_file():
        return None
    return candidate


def _started_at_label(value):
    if not value:
        return '-'
    import datetime
    timestamp = datetime.datetime.fromtimestamp(float(value), tz=datetime.timezone.utc)
    return timestamp.strftime('%Y-%m-%d %H:%M UTC')


def _telemetry_session_payload(session_id, session_dir):
    artifacts, paths = _telemetry_artifacts(session_dir)
    telemetry_meta = _read_json(session_dir / 'telemetry_meta.json') if session_dir else None
    telemetry_summary = _read_json(session_dir / 'telemetry_summary.json') if session_dir else None
    started_at_utc = None
    for payload in (telemetry_meta, telemetry_summary):
        if not payload:
            continue
        for key in ('actual_start_utc', 'start_time_utc', 'scheduled_start_utc'):
            value = payload.get(key)
            if isinstance(value, (int, float)) and value > 0:
                started_at_utc = float(value)
                break
        if started_at_utc:
            break
    missing = []
    if not artifacts['telemetry']:
        missing.append('telemetry')
    if not artifacts['telemetryMeta']:
        missing.append('telemetry meta')
    if not artifacts['telemetrySummary']:
        missing.append('telemetry summary')
    notes = 'Telemetry artifacts are present.' if not missing else 'Missing ' + ', '.join(missing) + '.'
    status = 'complete' if artifacts['telemetry'] and artifacts['telemetryMeta'] and artifacts['telemetrySummary'] else 'partial'
    return {
        'sessionId': session_id,
        'startedAtUtc': started_at_utc,
        'startedAtLabel': _started_at_label(started_at_utc),
        'status': status,
        'source': 'live',
        'artifacts': artifacts,
        'notes': notes,
        'description': 'Telemetry session view from the pypilot recording root.',
        'exportReady': False,
        'paths': paths,
        'metadata': {
            'telemetryMeta': telemetry_meta,
            'telemetrySummary': telemetry_summary,
            'videoMeta': None,
            'videoSummary': None,
        },
    }


def _telemetry_list_payload():
    root = _telemetry_root()
    session_dirs = _session_dirs(root)
    sessions = [_telemetry_session_payload(session_id, session_dir) for session_id, session_dir in session_dirs.items()]
    sessions.sort(key=lambda item: (-(item['startedAtUtc'] or 0.0), item['sessionId']))
    message = 'Found %d telemetry session(s).' % len(sessions)
    if not root.exists():
        message += ' Telemetry root unavailable: %s' % root
    return {'ok': True, 'liveSupported': True, 'message': message, 'sessions': sessions}

try:
    from flask_babel import Babel, gettext
    babel = Babel(app)

    LANGUAGES = os.listdir(os.path.dirname(os.path.abspath(__file__)) + '/translations')

    @babel.localeselector
    def get_locale():
        if 'language' in config:
            language = config['language']
        else:
            language = 'default'
        if language == 'default' or not language in LANGUAGES:
            return request.accept_languages.best_match(LANGUAGES)
        return language
    
except Exception as e:
    print('failed to import flask_babel, translations not possible!!', e)
    def _(x): return x
    app.jinja_env.globals.update(_=_)
    babel = None


@app.route('/logs')
def logs():
    log_links = ''
    try:
        logdirs = os.listdir('/var/log')
        for name in logdirs:
            if os.path.exists(os.path.join('/var/log', name, 'current')):
                log_links+='<br><a href="log/'+name+'">'+name+'</a>';
    except Exception as e:
        print('failed to enumerate log directory', e)

    return render_template('logs.html', async_mode=socketio.async_mode, log_links=Markup(log_links))

@app.route('/log/<name>')
def log(name):
    log = ''
    try:
        f = open('/var/log/' + name + '/current')
        log = f.read()
        f.close()
    except Exception as e:
        log = _('failed to read log file') + ' "' + name + '": ' + str(e)
    r = app.make_response(log)
    r.mimetype = 'text/plain'
    return r

    
@app.route('/wifi', methods=['GET', 'POST'])
def wifi():
    networking = '/home/tc/.pypilot/networking.txt'
    wifi = {'mode': 'Master', 'ssid': 'pypilot', 'key': '',
            'client_ssid': 'openplotter', 'client_key': '12345678', 'client_address': '10.10.10.60'}
    try:
        f = open(networking, 'r')
        while True:
            l = f.readline()
            if not l:
                break
            try:
                name, value = l.split('=')
                wifi[name] = value.rstrip()
            except Exception as e:
                print('failed to parse line in networking.txt', l)
        f.close()
    except:
        pass

    if request.method == 'POST':
        try:
            for name in request.form:
                wifi[name] = str(request.form[name])

            f = open(networking, 'w')
            for name in wifi:
                f.write(name+'='+wifi[name]+'\n')
            f.close()

            os.system('/opt/networking.sh')
        except Exception as e:
            print('exception!', e)

    try:
        leases = '<table id="leases">'
        leases += '<tr><th>IP Address</th><th>Mac Address</th><th>Name</th><th>Lease ends on</th></tr>'
        DNSMASQ_LEASES_FILE = "/var/lib/misc/dnsmasq.leases"
        f = open(DNSMASQ_LEASES_FILE)
        for line in f:
            elements = line.split()
            if len(elements) == 5:
                if elements[3] == "*":
                    continue
                
                from datetime import datetime
                ts = int(elements[0])
                if ts:
                    ts = datetime.utcfromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')
                else:
                    ts = 'Never'
                    
                leases += '<tr>'
                leases += '<td>' + elements[2] + '</td>'
                leases += '<td>' + elements[1] + '</td>'
                leases += '<td>' + elements[3] + '</td>'
                leases += '<td>' + ts + '</td>'
                leases += '</tr>'
        leases += '</table>'
    except Exception as e:
        print('lease fail', e)
        leases = ''
    if not 'Master' in wifi['mode']:
        leases = ''

    return render_template('wifi.html', async_mode=socketio.async_mode, wifi=Markup(wifi), leases=Markup(leases))

@app.route('/calibrationplot')
def calibrationplot():
    return render_template('calibrationplot.html', async_mode=socketio.async_mode,pypilot_web_port=pypilot_web_port)

@app.route('/client')
def client():
    return render_template('client.html', async_mode=socketio.async_mode,pypilot_web_port=pypilot_web_port)


@app.route('/api/telemetry-sessions')
def telemetry_sessions():
    return jsonify(_telemetry_list_payload())


@app.route('/api/telemetry-sessions/<path:session_id>')
def telemetry_session_detail(session_id):
    root = _telemetry_root()
    session_dir = _session_dirs(root).get(session_id)
    if not session_dir:
        return jsonify({'ok': False, 'message': 'Unknown session: ' + session_id}), 404
    return jsonify({'ok': True, 'session': _telemetry_session_payload(session_id, session_dir)})


@app.route('/api/telemetry-sessions/<path:session_id>/artifacts/<artifact_key>')
def telemetry_session_artifact(session_id, artifact_key):
    candidate = _telemetry_artifact_path(session_id, artifact_key)
    if not candidate:
        return jsonify({'ok': False, 'message': 'Unknown telemetry artifact: ' + artifact_key}), 404
    return send_file(str(candidate), as_attachment=True, download_name=candidate.name)


def _create_pypilot_client():
    factory = app.config.get('PYPILOT_CLIENT_FACTORY', pypilotClient)
    return factory()


def _normalize_requested_pypilot_values(names):
    if names is None:
        return []
    if isinstance(names, str):
        names = [names]
    if not isinstance(names, list):
        raise ValueError('Expected "get" to be a string or list of strings.')

    requested = []
    for name in names:
        if not isinstance(name, str) or not name:
            raise ValueError('Requested pypilot values must be non-empty strings.')
        if name not in requested:
            requested.append(name)
    return requested


def _normalize_pypilot_set_values(values):
    if values is None:
        return {}
    if not isinstance(values, dict):
        raise ValueError('Expected "set" to be an object of pypilot values.')

    normalized = {}
    for name, value in values.items():
        if not isinstance(name, str) or not name:
            raise ValueError('Pypilot value names must be non-empty strings.')
        normalized[name] = value
    return normalized


def _connect_pypilot_client(client, timeout=2.0):
    if client.connection:
        return True

    end = time.monotonic() + timeout
    while time.monotonic() < end:
        client.poll(min(.1, end - time.monotonic()))
        if client.connection:
            return True
    return bool(client.connection)


def _send_pypilot_value(client, name, value):
    client.send(name + '=' + pyjson.dumps(value) + '\n')


def _request_pypilot_values(client, requested, timeout=2.0):
    if not requested:
        return {}, []

    requested_set = set(requested)
    for name in requested:
        client.watch(name)

    values = {}
    end = time.monotonic() + timeout
    while len(values) < len(requested) and time.monotonic() < end:
        client.poll(min(.1, end - time.monotonic()))
        for name, value in client.receive().items():
            if name in requested_set:
                values[name] = value

    missing = [name for name in requested if name not in values]
    return values, missing


@app.route('/api/pypilot', methods=['POST'])
def pypilot_api():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({'ok': False, 'message': 'Expected a JSON object request body.'}), 400

    try:
        set_values = _normalize_pypilot_set_values(payload.get('set'))
        requested = _normalize_requested_pypilot_values(
            payload['get'] if 'get' in payload else payload.get('request')
        )
    except ValueError as e:
        return jsonify({'ok': False, 'message': str(e)}), 400

    if not set_values and not requested:
        return jsonify({'ok': False, 'message': 'Provide "set" and/or "get" in the request body.'}), 400

    client = _create_pypilot_client()
    try:
        if not _connect_pypilot_client(client):
            return jsonify({'ok': False, 'message': 'Unable to connect to pypilot.'}), 503

        for name, value in set_values.items():
            _send_pypilot_value(client, name, value)

        values, missing = _request_pypilot_values(client, requested)
    finally:
        client.disconnect()

    response = {'ok': not missing, 'values': values}
    if missing:
        response['missing'] = missing
        response['message'] = 'Timed out waiting for requested pypilot values.'
    return jsonify(response), 200 if not missing else 504

translations = []
static = False
with open(os.path.dirname(os.path.abspath(__file__)) + '/pypilot_web.pot') as f:
    for line in f:
        if line.startswith('#: static'):
            static = True
        elif len(line.strip()) == 0:
            static = False
        elif static and line.startswith('msgid'):
            s = line[7:-2]
            if s:
                translations.append(s)

@app.route('/')
def index():
    return render_template('index.html', async_mode=socketio.async_mode, pypilot_web_port=pypilot_web_port, tinypilot=tinypilot.tinypilot, translations=translations, language=config['language'], languages=Markup(LANGUAGES))

class pypilotWeb(Namespace):
    def __init__(self, name):
        super(Namespace, self).__init__(name)
        socketio.start_background_task(target=self.background_thread)
        self.clients = {}

    def background_thread(self):
        print('processing clients')
        x = 0
        while True:
            socketio.sleep(.25)
            sys.stdout.flush() # update log
            sids = list(self.clients)
            for sid in sids:
                if not sid in self.clients:
                    print('client was removed')
                    continue # was removed

                client = self.clients[sid]
                values = client.list_values()
                if values:
                    socketio.emit('pypilot_values', pyjson.dumps(values), room=sid)
                if not client.connection:
                    socketio.emit('pypilot_disconnect', room=sid)
                msgs = client.receive()
                if msgs:
                    # convert back to json (format is nicer)
                    #print('msgs', msgs, sid)
                    socketio.emit('pypilot', pyjson.dumps(msgs), room=sid)

    def on_pypilot(self, message):
        #print('message', message)
        self.clients[request.sid].send(message + '\n')

    def on_ping(self):
        emit('pong')

    def on_connect(self):
        print('Client connected', request.sid)
        client = pypilotClient()
        self.clients[request.sid] = client

    def on_disconnect(self):
        print('Client disconnected', request.sid)
        client = self.clients[request.sid]
        client.disconnect()
        del self.clients[request.sid]

    def on_language(self, language):
        config['language'] = language
        write_config()

socketio.on_namespace(pypilotWeb(''))

def main():
    import os
    path = os.path.dirname(__file__)
    os.chdir(os.path.abspath(path))
    port = pypilot_web_port
    while True:
        try:
            socketio.run(app, debug=False, host='0.0.0.0', port=port)
            break
        except PermissionError as e:
            print('failed to run socket io on port', port, e)
            port += 8000 - 80
            print('trying port', port)

if __name__ == '__main__':
    main()
