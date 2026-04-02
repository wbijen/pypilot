import datetime
import json
import os
import socket
import time

from values import BooleanProperty, EnumProperty, Property


def _bool_string(value):
    return 'true' if value else 'false'


def _safe_value(value):
    if value is False:
        return None
    return value


def _fix_field(fix, name):
    if not isinstance(fix, dict):
        return None
    return _safe_value(fix.get(name))


class RecordLogger(object):
    def __init__(self, ap):
        self.ap = ap
        self.root = os.getenv('PYPILOT_RECORD_ROOT', os.path.join(os.getenv('HOME'), '.pypilot', 'recordings'))
        self.arm_delay_sec = float(os.getenv('PYPILOT_RECORD_ARM_DELAY_SEC', '3.0'))
        self.sample_period = 1.0 / float(os.getenv('PYPILOT_RECORD_SAMPLE_HZ', '10.0'))
        self.sync_source = os.getenv('PYPILOT_RECORD_SYNC_SOURCE', 'external')
        self.session_dir = None
        self.telemetry_handle = None
        self.actual_start_utc = 0.0
        self.actual_start_monotonic = 0.0
        self.last_sample_monotonic = 0.0
        self.last_mode = 'off'

        self.mode = ap.register(EnumProperty, 'record.mode', 'off', ['off', 'record'], persistent=True)
        self.state = ap.register(Property, 'record.state', 'idle')
        self.session_id = ap.register(Property, 'record.session_id', '')
        self.reason = ap.register(Property, 'record.reason', '')
        self.storage_ok = ap.register(BooleanProperty, 'record.storage_ok', False)
        self.start_time_utc = ap.register(Property, 'record.start_time_utc', 0.0)
        self.actual_start_time_utc = ap.register(Property, 'record.actual_start_time_utc', 0.0)
        self.clock_sync_ok = ap.register(BooleanProperty, 'record.clock_sync_ok', False)
        self.clock_offset_ms = ap.register(Property, 'record.clock_offset_ms', 0.0)
        self.last_update = ap.register(Property, 'record.last_update', 0.0)

    def poll(self):
        now_utc = time.time()
        now_monotonic = time.monotonic()
        self.last_update.set(now_monotonic)

        if self.mode.value != 'record':
            if self.telemetry_handle:
                self._stop_recording('stopped')
            self._set_idle()
            self.last_mode = self.mode.value
            return

        sync_ok, offset_ms, sync_reason = self._query_clock_sync()
        self.clock_sync_ok.set(sync_ok)
        self.clock_offset_ms.set(offset_ms)

        storage_ok, storage_reason = self._check_storage()
        self.storage_ok.set(storage_ok)

        if not storage_ok:
            self._set_fault(storage_reason)
            return

        if self.state.value in ('idle', 'fault') or self.last_mode != 'record':
            if not self.session_id.value:
                self.session_id.set(self._generate_session_id())
            self.start_time_utc.set(now_utc + self.arm_delay_sec)
            self.actual_start_time_utc.set(0.0)
            self.reason.set('armed')
            self.state.set('armed')
            self.last_mode = 'record'
            return

        if self.state.value == 'armed':
            if now_utc >= self.start_time_utc.value:
                self._start_recording(now_utc, now_monotonic)
            return

        if self.state.value == 'recording':
            if now_monotonic - self.last_sample_monotonic >= self.sample_period:
                self._write_sample(now_utc, now_monotonic)
                self.last_sample_monotonic = now_monotonic
            return

        self.last_mode = self.mode.value

    def _set_idle(self):
        self.state.set('idle')
        self.session_id.set('')
        self.reason.set('')
        self.start_time_utc.set(0.0)
        self.actual_start_time_utc.set(0.0)

    def _set_fault(self, reason):
        self.state.set('fault')
        self.reason.set(reason)

    def _generate_session_id(self):
        utc = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H-%M-%SZ')
        host = socket.gethostname()
        return utc + '_' + host

    def _check_storage(self):
        try:
            os.makedirs(self.root, exist_ok=True)
            probe_path = os.path.join(self.root, '.record_probe')
            with open(probe_path, 'w') as handle:
                handle.write('ok\n')
            os.unlink(probe_path)
            return True, 'storage_ok'
        except Exception as e:
            return False, 'storage_unavailable: ' + str(e)

    def _query_clock_sync(self):
        return True, 0.0, self.sync_source + '_sync_managed'

    def _start_recording(self, now_utc, now_monotonic):
        self.session_dir = os.path.join(self.root, self.session_id.value)
        os.makedirs(self.session_dir, exist_ok=True)
        meta_path = os.path.join(self.session_dir, 'telemetry_meta.json')
        with open(meta_path, 'w') as handle:
            json.dump({
                'session_id': self.session_id.value,
                'hostname': socket.gethostname(),
                'scheduled_start_utc': self.start_time_utc.value,
                'actual_start_utc': now_utc,
                'actual_start_monotonic': now_monotonic,
                'clock_sync_ok': bool(self.clock_sync_ok.value),
                'clock_offset_ms': float(self.clock_offset_ms.value),
                'clock_sync_source': self.sync_source,
                'root': self.root,
            }, handle, indent=2, sort_keys=True)

        telemetry_path = os.path.join(self.session_dir, 'telemetry.jsonl')
        self.telemetry_handle = open(telemetry_path, 'w')
        self.actual_start_utc = now_utc
        self.actual_start_monotonic = now_monotonic
        self.actual_start_time_utc.set(now_utc)
        self.state.set('recording')
        self.reason.set('recording')
        self.last_sample_monotonic = 0.0

    def _stop_recording(self, reason):
        if self.telemetry_handle:
            try:
                self.telemetry_handle.flush()
                self.telemetry_handle.close()
            finally:
                self.telemetry_handle = None
        if self.session_dir:
            summary_path = os.path.join(self.session_dir, 'telemetry_summary.json')
            with open(summary_path, 'w') as handle:
                json.dump({
                    'session_id': self.session_id.value,
                    'end_utc': time.time(),
                    'reason': reason,
                }, handle, indent=2, sort_keys=True)
        self.session_dir = None
        self.actual_start_utc = 0.0
        self.actual_start_monotonic = 0.0

    def _sensor_value(self, values, name):
        if name not in values:
            return None
        return _safe_value(values[name].value)

    def _write_sample(self, now_utc, now_monotonic):
        if not self.telemetry_handle:
            return

        imu = self.ap.boatimu.SensorValues
        gps_fix = _safe_value(self.ap.sensors.gps.fix.value)
        sample = {
            'session_id': self.session_id.value,
            'timestamp_utc': now_utc,
            'timestamp_monotonic': now_monotonic,
            'session_elapsed': now_monotonic - self.actual_start_monotonic,
            'autopilot': {
                'enabled': bool(self.ap.enabled.value),
                'mode': self.ap.mode.value,
                'preferred_mode': self.ap.preferred_mode.value,
                'heading': _safe_value(self.ap.heading.value),
                'heading_command': _safe_value(self.ap.heading_command.value),
                'heading_error': _safe_value(self.ap.heading_error.value),
                'heading_error_int': _safe_value(self.ap.heading_error_int.value),
            },
            'imu': {
                'heading': self._sensor_value(imu, 'heading'),
                'heading_lowpass': self._sensor_value(imu, 'heading_lowpass'),
                'pitch': self._sensor_value(imu, 'pitch'),
                'roll': self._sensor_value(imu, 'roll'),
                'pitchrate': self._sensor_value(imu, 'pitchrate'),
                'rollrate': self._sensor_value(imu, 'rollrate'),
                'headingrate': self._sensor_value(imu, 'headingrate'),
                'headingrate_lowpass': self._sensor_value(imu, 'headingrate_lowpass'),
                'heel': self._sensor_value(imu, 'heel'),
                'accel': self._sensor_value(imu, 'accel'),
                'gyro': self._sensor_value(imu, 'gyro'),
                'compass': self._sensor_value(imu, 'compass'),
            },
            'gps': {
                'lat': _fix_field(gps_fix, 'lat'),
                'lon': _fix_field(gps_fix, 'lon'),
                'track': _safe_value(self.ap.sensors.gps.track.value),
                'speed': _safe_value(self.ap.sensors.gps.speed.value),
                'fix': gps_fix,
            },
            'servo': {
                'state': self.ap.servo.state.value,
                'engaged': bool(self.ap.servo.engaged.value),
                'position': _safe_value(self.ap.servo.position.value),
                'raw_command': _safe_value(self.ap.servo.rawcommand.value),
                'command': _safe_value(self.ap.servo.command.value),
            },
            'jetson': {
                'mode': self.ap.jetson_mode.value,
                'state': self.ap.jetson_state.value,
                'confidence': _safe_value(self.ap.jetson_confidence.value),
                'heading_offset': _safe_value(self.ap.jetson_heading_offset.value),
                'target_heading': _safe_value(self.ap.jetson_target_heading.value),
            },
        }
        self.telemetry_handle.write(json.dumps(sample, separators=(',', ':')) + '\n')
        self.telemetry_handle.flush()
