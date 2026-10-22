#!/usr/bin/env python3
"""
Domoticz Zigbee-Watchdog
Status anhand von Zigbee2MQTT last_seen; Wiederherstellung einzelner Geräte wird erkannt.
"""
"""
<plugin key="ZigbeeWatchdog" name="Zigbee-Watchdog" author="schurgan/OpenAI" version="2.5.4" wikilink="" externallink="">
    <description>
        Überwacht Zigbee-Geräte anhand von Zigbee2MQTT last_seen.
    </description>
    <params>
        <param field="Address" label="MQTT-Broker" width="200px" required="true" default="127.0.0.1"/>
        <param field="Port" label="MQTT-Port" width="75px" required="true" default="1883"/>
        <param field="Mode1" label="MQTT-Basistopic" width="200px" required="true" default="zigbee2mqtt"/>
        <param field="Mode6" label="Dashboard-Geräte-Idx" width="80px" required="true" default="2548"/>
        <param field="Mode7" label="Domoticz-URL" width="200px" required="true" default="http://127.0.0.1:8080"/>
        <param field="Mode8" label="Debug (0/1)" width="50px" required="true" default="0"/>
        <param field="Mode9" label="Repeater offline Timeout (Sek.)" width="100px" required="true" default="900"/>
        <param field="Mode10" label="Sensor offline Timeout (Sek.)" width="100px" required="true" default="14400"/>
        <param field="Mode11" label="Repeater-Prüfung nach Neustart (Sek.)" width="120px" required="true" default="600"/>
        <param field="Mode12" label="Sensor-Prüfung nach Neustart (Sek.)" width="120px" required="true" default="3600"/>
        <param field="Mode13" label="Wartezeit bis Neustart (Sek.)" width="120px" required="true" default="60"/>
        <param field="Mode14" label="Max. Neustartversuche (1-3)" width="100px" required="true" default="3"/>
    </params>
</plugin>
"""

import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

import Domoticz
import paho.mqtt.client as mqtt
import threading
import subprocess

VERSION = "2.5.4"


class BasePlugin:
    def __init__(self):
        self.mqtt = None
        self.connected = False
        self.heartbeat_counter = 0
        self.mqtt_lock = threading.Lock()
        self.persist_lock = threading.Lock()
        self.last_seen = {}
        self.monitored = {}
        self.zigbee_devices = {}
        self.last_device_scan = 0
        self.last_health = None
        self.last_persist = 0
        self.base_topic = "zigbee2mqtt"
        self.health_idx = 2548
        self.domoticz_url = "http://127.0.0.1:8080"
        self.scan_interval = 60
        self.debug = False
        self.repeater_timeout = 900
        self.sensor_timeout = 14400
        self.repeater_recheck_seconds = 600
        self.sensor_recheck_seconds = 3600
        self.restart_delay_seconds = 60
        self.max_restart_attempts = 3
        self.restart_attempts = 0
        self.restart_pending = False
        self.restart_due = 0
        self.restart_started_at = 0
        self.recovery_due = 0
        self.repeater_check_due = 0
        self.sensor_check_due = 0
        self.recovery_waiting = False
        self.repeater_check_due = 0
        self.sensor_check_due = 0
        self.incident_repeater_trigger = False
        self.active_incident = False
        self.incident_condition = None
        self.incident_repeater_trigger = False
        self.last_alerted_offline = set()
        self.recovery_notified = False
        self.last_seen_path = Path(__file__).resolve().parent / "zigbee2mqtt_last_seen.json"
        self.offline_state_path = Path(__file__).resolve().parent / "zigbee2mqtt_offline_state.json"
        self.previous_offline_state = None
        self.offline_alert_state_path = Path(__file__).resolve().parent / "zigbee2mqtt_alert_state.json"
        self.offline_alert_state = {"devices": {}, "last_summary": 0}

    def log(self, message):
        Domoticz.Log("[Zigbee-Watchdog] " + str(message))

    def debug_log(self, message):
        if self.debug:
            self.log("DEBUG: " + str(message))

    def onStart(self):
        self.base_topic = (Parameters.get("Mode1", "zigbee2mqtt") or "zigbee2mqtt").strip("/")
        self.sensor_timeout = int(Parameters.get("Mode10", "14400") or "14400")
        self.repeater_timeout = int(Parameters.get("Mode9", "900") or "900")
        self.repeater_recheck_seconds = max(1, int(Parameters.get("Mode11", "600") or "600"))
        self.sensor_recheck_seconds = max(60, int(Parameters.get("Mode12", "3600") or "3600"))
        self.restart_delay_seconds = max(0, int(Parameters.get("Mode13", "60") or "60"))
        self.max_restart_attempts = min(3, max(1, int(Parameters.get("Mode14", "3") or "3")))
        self.health_idx = int(Parameters.get("Mode6", "2548") or "2548")
        self.domoticz_url = (Parameters.get("Mode7", "http://127.0.0.1:8080") or "http://127.0.0.1:8080").rstrip("/")
        self.debug = (Parameters.get("Mode8", "0") or "0") == "1"

        try:
            self.last_seen_path.parent.mkdir(parents=True, exist_ok=True)
            if self.last_seen_path.exists():
                with self.last_seen_path.open("r", encoding="utf-8") as f:
                    saved = json.load(f)
                if isinstance(saved, dict):
                    self.last_seen = saved
            self.log("Gespeicherte last_seen-Werte: {}".format(len(self.last_seen)))
            if self.offline_state_path.exists():
                with self.offline_state_path.open("r", encoding="utf-8") as f:
                    saved_offline = json.load(f)
                if isinstance(saved_offline, dict):
                    self.previous_offline_state = {
                        "sensor_offline": set(saved_offline.get("sensor_offline", [])),
                        "repeater_offline": set(saved_offline.get("repeater_offline", [])),
                    }
                    self.log("Gespeicherter Offline-Status geladen: {} Sensoren, {} Repeater".format(
                        len(self.previous_offline_state["sensor_offline"]),
                        len(self.previous_offline_state["repeater_offline"])
                    ))
        except Exception as e:
            self.log("Fehler beim Laden der last_seen-Datei: {}".format(e))

        address = Parameters.get("Address", "127.0.0.1") or "127.0.0.1"
        port = 1883
        client_id = "domoticz-zigbee-watchdog"
        try:
            self.mqtt = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION1,
                client_id=client_id,
                protocol=mqtt.MQTTv311
            )
        except (AttributeError, TypeError):
            self.mqtt = mqtt.Client(client_id=client_id, protocol=mqtt.MQTTv311)

        self.mqtt.on_connect = self._mqtt_on_connect
        self.mqtt.on_disconnect = self._mqtt_on_disconnect
        self.mqtt.on_message = self._mqtt_on_message
        self.mqtt.reconnect_delay_set(min_delay=1, max_delay=30)
        self.mqtt.connect_async(address, port, keepalive=60)
        self.mqtt.loop_start()
        Domoticz.Heartbeat(10)
        try:
            if self.offline_alert_state_path.exists():
                with self.offline_alert_state_path.open("r", encoding="utf-8") as f:
                    saved_alerts = json.load(f)
                if isinstance(saved_alerts, dict):
                    self.offline_alert_state.update(saved_alerts)
        except Exception as e:
            self.log("Offline-Meldungsstatus konnte nicht geladen werden: {}".format(e))
        self.log("Version {} gestartet; paho-mqtt {}:{}, Topic {}/+".format(
            VERSION, address, port, self.base_topic
        ))

    def onStop(self):
        try:
            if self.mqtt is not None:
                self.mqtt.disconnect()
                self.mqtt.loop_stop()
        except Exception as e:
            self.log("Fehler beim Beenden von paho-mqtt: {}".format(e))
        self.persist_last_seen(force=True)
        self.log("Gestoppt")

    def _mqtt_on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.connected = True
            topics = [
                (self.base_topic + "/+", 0),
                (self.base_topic + "/bridge/devices", 0)
            ]
            result, mid = client.subscribe(topics)
            if result == mqtt.MQTT_ERR_SUCCESS:
                self.log("MQTT verbunden; Topics abonniert: {}/+ und {}/bridge/devices".format(
                    self.base_topic, self.base_topic
                ))
            else:
                self.log("MQTT SUBSCRIBE fehlgeschlagen: rc={}".format(result))
        else:
            self.connected = False
            self.log("MQTT-Verbindung fehlgeschlagen: rc={}".format(rc))

    def _mqtt_on_disconnect(self, client, userdata, rc):
        self.connected = False
        if rc != 0:
            self.log("MQTT unerwartet getrennt (rc={}); paho versucht Reconnect".format(rc))
        else:
            self.log("MQTT-Verbindung getrennt")

    def _mqtt_on_message(self, client, userdata, message):
        data = {
            "Topic": message.topic,
            "Payload": message.payload.decode("utf-8", errors="replace")
        }
        self.onMessage(None, data)

    def onMessage(self, Connection, Data):
        try:
            topic = Data.get("Topic", "")
            payload = Data.get("Payload", "")
            self.debug_log("MQTT-Nachricht empfangen: Topic={}".format(topic))

            devices_topic = self.base_topic + "/bridge/devices"
            if topic == devices_topic:
                self.update_zigbee_device_list(payload)
                return

            prefix = self.base_topic + "/"
            if not topic.startswith(prefix):
                return

            friendly_name = topic[len(prefix):]
            if not friendly_name or "/" in friendly_name:
                return

            data = json.loads(payload)
            if not isinstance(data, dict) or data.get("last_seen") is None:
                return

            epoch = self.parse_last_seen(data["last_seen"])
            if epoch is None:
                self.debug_log("Ungültiges last_seen für {}: {}".format(
                    friendly_name, data.get("last_seen")
                ))
                return

            old = self.last_seen.get(friendly_name, {})
            self.last_seen[friendly_name] = {
                "epoch": epoch,
                "raw": str(data["last_seen"]),
                "received_at": int(time.time())
            }
            if old.get("epoch") != epoch:
                self.debug_log("{}: last_seen={}".format(
                    friendly_name, data["last_seen"]
                ))
            self.persist_last_seen()
        except Exception as e:
            self.log("MQTT-Nachricht konnte nicht verarbeitet werden: {}".format(e))

    @staticmethod
    def parse_last_seen(value):
        try:
            if isinstance(value, (int, float)):
                return int(value / 1000) if value > 100000000000 else int(value)
            value = str(value).strip()
            if value.endswith("Z"):
                value = value[:-1] + "+00:00"
            dt = datetime.fromisoformat(value)
            if dt.tzinfo is None:
                return int(time.mktime(dt.timetuple()))
            return int(dt.timestamp())
        except (ValueError, TypeError, OverflowError):
            return None

    def persist_last_seen(self, force=False):
        if self.last_seen_path is None:
            return

        # MQTT callback and Domoticz heartbeat can run on different threads.
        # Serialize writes so both never use the same temporary file at once.
        with self.persist_lock:
            now = int(time.time())
            if not force and now - self.last_persist < 5:
                return
            try:
                temp_path = self.last_seen_path.with_name(
                    self.last_seen_path.name + ".tmp"
                )
                with temp_path.open("w", encoding="utf-8") as f:
                    json.dump(self.last_seen, f, ensure_ascii=False)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(str(temp_path), str(self.last_seen_path))
                self.last_persist = now
            except Exception as e:
                self.log("Fehler beim Speichern der last_seen-Datei: {}".format(e))

    def fetch_domoticz_devices(self):
        query = urllib.parse.urlencode({
            "type": "command",
            "param": "getdevices",
            "filter": "all",
            "used": "true",
            "order": "Name"
        })
        url = self.domoticz_url + "/json.htm?" + query
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "Domoticz-Zigbee-Watchdog"}
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                result = json.loads(response.read().decode("utf-8"))
            if result.get("status") != "OK":
                self.debug_log("Domoticz-Geräteliste: API-Status nicht OK")
                return None
            return result.get("result", [])
        except Exception as e:
            self.log("Domoticz-Geräteliste nicht erreichbar: {}".format(e))
            return None

    def update_zigbee_device_list(self, payload):
        try:
            devices = json.loads(payload)
            if not isinstance(devices, list):
                self.log("Zigbee2MQTT bridge/devices lieferte keine Liste")
                return

            discovered = {}
            for device in devices:
                if not isinstance(device, dict):
                    continue
                friendly = str(device.get("friendly_name", "")).strip()
                device_type = str(device.get("type", "")).strip().casefold()
                if not friendly or device_type == "coordinator":
                    continue

                # Stromversorgung entscheidet: netzbetriebene Geräte zählen
                # als Repeater, batteriebetriebene Geräte als Sensoren.
                # Zigbee2MQTT kann netzbetriebene Geräte trotzdem als EndDevice melden.
                power_source = str(device.get("power_source", "")).strip().casefold()
                mains_powered = (
                    "mains" in power_source
                    or "line" in power_source
                    or "ac" == power_source
                    or "plug" in power_source
                )
                kind = "repeater" if mains_powered else "sensor"
                discovered[friendly] = {
                    "name": friendly,
                    "kind": kind,
                    "zigbee_type": device.get("type", "")
                }

            self.zigbee_devices = discovered
            self.refresh_monitored()
            self.log("Zigbee2MQTT-Geräteliste empfangen: {} Geräte".format(len(discovered)))
        except Exception as e:
            self.log("Zigbee2MQTT-Geräteliste konnte nicht verarbeitet werden: {}".format(e))

    def refresh_monitored(self):
        # Die vollständige Geräteliste kommt direkt von zigbee2mqtt/bridge/devices.
        # last_seen ist nur der Status-/Zeitstempelbestandteil, nicht die Geräteliste.
        monitored = {}
        for friendly, entry in self.zigbee_devices.items():
            monitored[friendly] = dict(entry)

        self.monitored = monitored
        self.log("Zigbee2MQTT-Geräte überwacht: {}".format(len(monitored)))

    def update_health_device(self, level, message):
        query = urllib.parse.urlencode({
            "type": "command",
            "param": "udevice",
            "idx": self.health_idx,
            "nvalue": level,
            "svalue": message
        })
        url = self.domoticz_url + "/json.htm?" + query
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "Domoticz-Zigbee-Watchdog"}
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                result = json.loads(response.read().decode("utf-8"))
            if result.get("status") != "OK":
                self.log("Dashboard-Update nicht bestätigt: {}".format(result))
        except Exception as e:
            self.log("Dashboard-Update fehlgeschlagen: {}".format(e))

    def send_telegram(self, subject, body):
        query = urllib.parse.urlencode({
            "type": "command",
            "param": "sendnotification",
            "subject": subject,
            "body": body,
            "subsystem": "telegram"
        })
        url = self.domoticz_url + "/json.htm?" + query
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Domoticz-Zigbee-Watchdog"})
            with urllib.request.urlopen(req, timeout=10) as response:
                result = json.loads(response.read().decode("utf-8"))
            if result.get("status") == "OK":
                self.log("Telegram gesendet: {}".format(subject))
                return True
            self.log("Telegram-Versand nicht bestätigt: {}".format(result))
        except Exception as e:
            self.log("Telegram-Versand fehlgeschlagen: {}".format(e))
        return False

    def restart_zigbee2mqtt(self):
        try:
            result = subprocess.run(
                ["/bin/systemctl", "restart", "zigbee2mqtt.service"],
                capture_output=True, text=True, timeout=60, check=False
            )
            if result.returncode == 0:
                self.log("zigbee2mqtt.service wurde neu gestartet")
                return True
            self.log("Neustart von zigbee2mqtt.service fehlgeschlagen: {}".format(
                (result.stderr or result.stdout or "unbekannter Fehler").strip()
            ))
        except Exception as e:
            self.log("Fehler beim Neustart von zigbee2mqtt.service: {}".format(e))
        return False

    def current_offline_state(self, now):
        sensor_total = 0
        repeater_total = 0
        sensor_offline_names = []
        repeater_offline_names = []
        for friendly, entry in self.monitored.items():
            kind = entry["kind"]
            if kind == "repeater":
                repeater_total += 1
            else:
                sensor_total += 1

            record = self.last_seen.get(friendly)
            if not record or not record.get("epoch"):
                continue

            age = max(0, now - int(record["epoch"]))
            timeout = self.repeater_timeout if kind == "repeater" else self.sensor_timeout
            offline = age >= timeout if kind == "repeater" else age > timeout
            if offline:
                if kind == "repeater":
                    repeater_offline_names.append(friendly)
                else:
                    sensor_offline_names.append(friendly)

        return {
            "sensor_total": sensor_total,
            "repeater_total": repeater_total,
            "sensor_offline": sensor_offline_names,
            "repeater_offline": repeater_offline_names
        }

    def restart_condition_met(self, state):
        return (
            (len(state["repeater_offline"]) >= 1 and len(state["sensor_offline"]) >= 4)
            or len(state["sensor_offline"]) >= 5
        )

    def notify_offline_changes_and_reminders(self, now, state):
        """Meldet jedes neue Offline-Gerät sofort und erinnert gestaffelt."""
        current = {}
        for kind, names in (("Sensor", state["sensor_offline"]), ("Repeater", state["repeater_offline"])):
            for name in names:
                current[name] = kind

        tracked = self.offline_alert_state.setdefault("devices", {})
        previous_names = set(tracked)
        current_names = set(current)
        new_names = sorted(current_names - previous_names)
        recovered_names = sorted(previous_names - current_names)

        if new_names:
            lines = [
                "⚠️ Zigbee Watchdog",
                "━━━━━━━━━━━━━━━━━━━━",
                "Offline gesamt: {} (gesamt {})".format(len(current_names), len(self.monitored)),
                "Zeit: {}".format(datetime.now().strftime("%d.%m.%y | %H:%M")),
                "",
                "🔔 Aktive Meldungen",
            ]
            for name in new_names:
                tracked[name] = {"kind": current[name], "since": now, "reminders": 0, "next_due": now + 900}
            sensors = sorted(name for name, kind in current.items() if kind == "Sensor")
            repeaters = sorted(name for name, kind in current.items() if kind == "Repeater")
            if sensors:
                lines.extend(["", "🔋 SENSOREN ({})".format(len(sensors))])
                lines.extend("🔴 {}".format(name) for name in sensors)
            if repeaters:
                lines.extend(["", "🔌 REPEATER ({})".format(len(repeaters))])
                lines.extend("🔴 {}".format(name) for name in repeaters)
            self.send_telegram("⚠️ Zigbee-Watchdog | Offline-Geräte", "\n".join(lines))
            self.log("Neue Offline-Geräte gemeldet: {}".format(", ".join(new_names)))

        if recovered_names:
            lines = [
                "⚠️ Zigbee Watchdog",
                "━━━━━━━━━━━━━━━━━━━━",
                "Offline gesamt: {} (gesamt {})".format(len(current_names), len(self.monitored)),
                "Zeit: {}".format(datetime.now().strftime("%d.%m.%y | %H:%M")),
                "",
                "🟢 Wieder erreichbar",
            ]
            for name in recovered_names:
                lines.append("• {}".format(name))
                tracked.pop(name, None)
            self.send_telegram("✅ Zigbee-Watchdog | Geräte wieder erreichbar", "\n".join(lines))
            self.log("Wieder erreichbare Geräte gemeldet: {}".format(", ".join(recovered_names)))

        due = [name for name in sorted(current_names) if name in tracked and now >= int(tracked[name].get("next_due", now + 900))]
        if due:
            lines = [
                "⚠️ Zigbee Watchdog",
                "━━━━━━━━━━━━━━━━━━━━",
                "Offline gesamt: {} (gesamt {})".format(len(current_names), len(self.monitored)),
                "Zeit: {}".format(datetime.now().strftime("%d.%m.%y | %H:%M")),
                "",
                "🔔 Aktive Meldungen",
                "🟠 Weiterhin offline",
            ]
            due_set = set(due)
            for kind, heading, icon in (("Sensor", "🔋 SENSOREN", "🔴"), ("Repeater", "🔌 REPEATER", "🟠")):
                active_kind = sorted(name for name, current_kind in current.items() if current_kind == kind)
                if not active_kind:
                    continue
                lines.extend(["", "{} ({})".format(heading, len(active_kind))])
                for name in active_kind:
                    if name in due_set:
                        item = tracked[name]
                        item["reminders"] = int(item.get("reminders", 0)) + 1
                        # First three reminders every 15 minutes, then hourly.
                        item["next_due"] = now + (900 if item["reminders"] < 3 else 3600)
                        elapsed = max(0, now - int(item.get("since", now)))
                        lines.append("{} {} — seit {} Min. offline (Erinnerung)".format(icon, name, elapsed // 60))
                    else:
                        lines.append("⚪ {} — weiterhin offline".format(name))
            self.send_telegram("⏰ Zigbee-Watchdog | Offline-Erinnerung", "\n".join(lines))
            self.log("Offline-Erinnerung gesendet für: {}".format(", ".join(due)))

        self.offline_alert_state["devices"] = tracked
        try:
            temp = self.offline_alert_state_path.with_name(self.offline_alert_state_path.name + ".tmp")
            with temp.open("w", encoding="utf-8") as f:
                json.dump(self.offline_alert_state, f, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(str(temp), str(self.offline_alert_state_path))
        except Exception as e:
            self.log("Offline-Meldungsstatus konnte nicht gespeichert werden: {}".format(e))

    def process_recovery(self, now, state):
        repeater_offline = len(state["repeater_offline"])
        sensor_offline = len(state["sensor_offline"])
        repeater_trigger = repeater_offline >= 1 and sensor_offline >= 4
        sensor_trigger = sensor_offline >= 5
        condition = repeater_trigger or sensor_trigger

        if not self.active_incident and condition:
            self.active_incident = True
            self.restart_attempts = 0
            self.restart_pending = True
            self.restart_due = now + self.restart_delay_seconds
            self.recovery_waiting = False
            self.incident_repeater_trigger = repeater_trigger
            self.incident_sensor_trigger = sensor_trigger
            self.sensor_recovery_notified = False
            self.repeater_recovery_notified = False
            self.send_telegram(
                "Zigbee-Watchdog - STOERUNG ERKANNT",
                "Die Offline-Schwelle wurde erreicht.\n\n"
                "Repeater offline ({}): {}\n"
                "Sensoren offline ({}): {}\n\n"
                "Geplant: Neustartversuch 1/{} in {} Sekunden.".format(
                    repeater_offline,
                    ", ".join(state["repeater_offline"]) or "keine",
                    sensor_offline,
                    ", ".join(state["sensor_offline"]) or "keine",
                    self.max_restart_attempts, self.restart_delay_seconds
                )
            )
            self.log("Ausfallbedingung erfüllt; Neustart geplant")

        if not self.active_incident:
            return

        # Send recovery notice when the specific condition(s) that triggered this
        # incident have cleared, even if some devices from the other category remain offline.
        repeater_condition_cleared = (
            not self.incident_repeater_trigger
            or repeater_offline == 0
            or sensor_offline < 4
        )
        sensor_condition_cleared = (
            not self.incident_sensor_trigger
            or sensor_offline < 5
        )
        incident_cleared = repeater_condition_cleared and sensor_condition_cleared

        if incident_cleared:
            had_work_pending = self.restart_pending or self.recovery_waiting
            self.restart_pending = False
            self.recovery_waiting = False
            self.active_incident = False
            self.restart_attempts = 0
            if had_work_pending:
                self.send_telegram(
                    "Zigbee-Watchdog - ENTWARNUNG",
                    "Die Ausfallbedingung ist nicht mehr erfüllt.\n\n"
                    "Repeater weiterhin offline ({}): {}\n"
                    "Sensoren weiterhin offline ({}): {}\n\n"
                    "Automatische Neustarts für diesen Vorfall wurden beendet.".format(
                        repeater_offline,
                        ", ".join(state["repeater_offline"]) or "keine",
                        sensor_offline,
                        ", ".join(state["sensor_offline"]) or "keine"
                    )
                )
                self.log("Entwarnung gesendet; auslösende Ausfallbedingung beendet")
            return

        if self.restart_pending and now >= self.restart_due:
            self.restart_attempts += 1
            attempt = self.restart_attempts
            self.send_telegram(
                "Zigbee-Watchdog - Neustart {}/{}".format(
                    attempt, self.max_restart_attempts
                ),
                "Zigbee2MQTT wird neu gestartet.\n\n"
                "Dienst: zigbee2mqtt.service\n"
                "Versuch: {}/{}".format(attempt, self.max_restart_attempts)
            )
            success = self.restart_zigbee2mqtt()
            self.restart_pending = False
            self.restart_started_at = now
            self.recovery_waiting = True
            self.repeater_check_due = now + self.repeater_recheck_seconds
            self.sensor_check_due = now + self.sensor_recheck_seconds
            self.send_telegram(
                "Zigbee-Watchdog - Neustart {}".format(
                    "gestartet" if success else "fehlgeschlagen"
                ),
                "Versuch: {}/{}\n"
                "Repeater-Kontrolle: nach {} Sekunden\n"
                "Sensor-Kontrolle: frühestens nach {} Sekunden\n\n"
                "Ein weiterer Neustart erfolgt nicht vor Ablauf der Sensor-Wartezeit.".format(
                    attempt, self.max_restart_attempts,
                    self.repeater_recheck_seconds, self.sensor_recheck_seconds
                )
            )
            return

        if not self.recovery_waiting:
            return

        # Never start another attempt before the full sensor waiting period.
        if now < self.sensor_check_due:
            return

        # Re-evaluate the current threshold condition after the waiting period.
        if not self.restart_condition_met(state):
            self.recovery_waiting = False
            self.active_incident = False
            self.restart_attempts = 0
            self.send_telegram(
                "Zigbee-Watchdog - ENTWARNUNG",
                "Nach dem Neustart ist die Ausfallbedingung nicht mehr erfüllt.\n\n"
                "Repeater offline ({}): {}\n"
                "Sensoren offline ({}): {}.".format(
                    repeater_offline,
                    ", ".join(state["repeater_offline"]) or "keine",
                    sensor_offline,
                    ", ".join(state["sensor_offline"]) or "keine"
                )
            )
            self.log("Entwarnung nach Wartezeit gesendet")
            return

        if self.restart_attempts < self.max_restart_attempts:
            self.recovery_waiting = False
            self.restart_pending = True
            self.restart_due = now + self.restart_delay_seconds
            self.send_telegram(
                "Zigbee-Watchdog - STOERUNG BESTEHT WEITER",
                "Auch nach {} Sekunden ist die Offline-Schwelle noch erreicht.\n\n"
                "Repeater offline: {}\n"
                "Sensoren offline: {}\n\n"
                "Nächster Neustartversuch: {}/{} in {} Sekunden.".format(
                    self.sensor_recheck_seconds, repeater_offline, sensor_offline,
                    self.restart_attempts + 1, self.max_restart_attempts,
                    self.restart_delay_seconds
                )
            )
        else:
            self.recovery_waiting = False
            self.active_incident = False
            self.send_telegram(
                "Zigbee-Watchdog - NEUSTARTLIMIT ERREICHT",
                "Die Störung besteht weiterhin. Es wurden alle {} Neustartversuche ausgeschöpft.\n\n"
                "Repeater offline: {}\n"
                "Sensoren offline: {}\n\n"
                "Es erfolgen keine weiteren automatischen Neustarts. Bitte Anlage prüfen.".format(
                    self.max_restart_attempts, repeater_offline, sensor_offline
                )
            )
            self.log("Maximale Neustartversuche erreicht; Automatik beendet")

    def onHeartbeat(self):
        self.heartbeat_counter += 1
        now = int(time.time())

        if now - self.last_device_scan >= self.scan_interval:
            self.refresh_monitored()
            self.last_device_scan = now

        if not self.monitored:
            return

        state = self.current_offline_state(now)
        sensor_total = state["sensor_total"]
        repeater_total = state["repeater_total"]
        sensor_offline = len(state["sensor_offline"])
        repeater_offline = len(state["repeater_offline"])
        offline_total = sensor_offline + repeater_offline

        if offline_total >= 3:
            level, status = 4, "KRITISCH"
        elif offline_total >= 1:
            level, status = 2, "WARNUNG"
        else:
            level, status = 1, "OK"

        message = "{} | Sens.:{}/{} | Rep.:{}/{}".format(
            status,
            sensor_offline, sensor_total,
            repeater_offline, repeater_total
        )

        if message != self.last_health:
            self.update_health_device(level, message)
            self.last_health = message
            self.log(message)

        self.notify_offline_changes_and_reminders(now, state)
        self.process_recovery(now, state)
        self.persist_last_seen()



_plugin = BasePlugin()


def onStart():
    _plugin.onStart()


def onStop():
    _plugin.onStop()


def onMessage(Connection, Data):
    _plugin.onMessage(Connection, Data)


def onHeartbeat():
    _plugin.onHeartbeat()
