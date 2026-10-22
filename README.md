# Zigbee-Watchdog für Domoticz

[![Version](https://img.shields.io/badge/version-2.5.4-blue.svg)](#versionierung)
[![Python](https://img.shields.io/badge/python-3.x-blue.svg)](https://www.python.org/)
[![Domoticz](https://img.shields.io/badge/platform-Domoticz-informational.svg)](https://www.domoticz.com/)
[![MQTT](https://img.shields.io/badge/protocol-MQTT-orange.svg)](https://mqtt.org/)

Ein Domoticz-Plugin zur Überwachung von Zigbee-Geräten, die über Zigbee2MQTT eingebunden sind. Der Watchdog wertet die Zeitstempel aus `last_seen` aus, erkennt offline gegangene und wieder erreichbare Geräte, meldet Statusänderungen per Telegram und aktualisiert ein Domoticz-Anzeige-Device.

> **Wichtig:** Dieses Projekt ist ein eigenständiger Plugin. Es ist nicht der reguläre [Zigbee2MQTT-Domoticz-Plugin](https://github.com/schurgan/zigbee2mqtt-plugin).

## Funktionen

- Automatische Erkennung der Zigbee-Geräte über `zigbee2mqtt/bridge/devices`
- Zuordnung zu **Sensoren** und **Repeatern** anhand der von Zigbee2MQTT gelieferten Geräteinformationen
- Überwachung über `last_seen` mit getrennt konfigurierbaren Zeitlimits
- Telegram-Meldungen bei neu erkannten Offline-Geräten und bei Wiederherstellung
- Gruppierte, kompakte Meldungen mit getrennter Auflistung von Sensoren und Repeatern
- Erinnerungen bei anhaltenden Offline-Zuständen
- Persistenz von Statusdaten, damit Zustandsvergleiche Plugin-Neustarts überstehen
- Domoticz-Health-Device mit kompakter Statuszeile, z. B. `OK | Sens.:0/80 | Rep.:0/33`
- Optionale automatische Neustarts von `zigbee2mqtt.service` mit begrenzter Versuchszahl und Warte-/Prüfzeiten

## Voraussetzungen

- Laufendes Domoticz mit Unterstützung für Python-Plugins
- Laufendes Zigbee2MQTT
- MQTT-Broker, den sowohl Domoticz als auch Zigbee2MQTT erreichen können
- In Domoticz konfigurierte Telegram-Benachrichtigungen, falls Telegram-Meldungen gewünscht sind
- Berechtigung des Domoticz-Prozesses zum Neustart von `zigbee2mqtt.service`, falls die automatische Neustartfunktion verwendet werden soll

Der Plugin verwendet `paho-mqtt`. Ob das Modul verfügbar ist, hängt von der Python-Umgebung ab, mit der Domoticz den Plugin ausführt.

## Installation

1. Lege im Domoticz-Plugin-Verzeichnis einen Ordner `Zigbee-Watchdog` an.
2. Kopiere `plugin.py` in diesen Ordner.
3. Stelle sicher, dass der Domoticz-Prozess die Datei lesen und in das Plugin-Verzeichnis schreiben darf. Der Plugin legt dort Zustandsdateien ab.
4. Starte Domoticz neu oder lade die Hardware-/Plugin-Konfiguration neu.
5. Füge in Domoticz unter **Einstellungen → Hardware** neue Hardware vom Typ **Zigbee-Watchdog** hinzu.
6. Trage MQTT-Verbindung, Domoticz-URL und die gewünschten Grenzwerte ein.
7. Prüfe das Domoticz-Protokoll auf Meldungen mit dem Präfix `[Zigbee-Watchdog]`.

> Passe den Installationspfad an deine Domoticz-Installation an. Verwende nicht den Ordner des regulären `zigbee2mqtt-plugin`.

## Konfiguration

Die Parameter werden in der Hardware-Konfiguration von Domoticz gesetzt.

| Parameter | Standard | Bedeutung |
|---|---:|---|
| MQTT-Broker (`Address`) | `127.0.0.1` | Hostname oder IP-Adresse des MQTT-Brokers |
| MQTT-Port (`Port`) | `1883` | MQTT-Port |
| MQTT-Basistopic (`Mode1`) | `zigbee2mqtt` | Zigbee2MQTT-Basistopic |
| Dashboard-Device-Idx (`Mode6`) | `2548` | IDX des Domoticz-Anzeige-Devices |
| Domoticz-URL (`Mode7`) | `http://127.0.0.1:8080` | URL der Domoticz-JSON-API |
| Debug (`Mode8`) | `0` | `1` aktiviert zusätzliche Debug-Protokollierung |
| Repeater-Timeout (`Mode9`) | `900` Sekunden | Zeit ohne `last_seen`, bevor ein Repeater als offline gilt |
| Sensor-Timeout (`Mode10`) | `14400` Sekunden | Zeit ohne `last_seen`, bevor ein Sensor als offline gilt |
| Repeater-Prüfung (`Mode11`) | `600` Sekunden | Wartezeit bis zur Repeater-Prüfung nach einem Neustart |
| Sensor-Prüfung (`Mode12`) | `3600` Sekunden | Mindestwartezeit bis zur Sensor-Prüfung nach einem Neustart |
| Neustart-Verzögerung (`Mode13`) | `60` Sekunden | Verzögerung vor einem geplanten Neustart |
| Max. Neustartversuche (`Mode14`) | `3` | Begrenzte Anzahl an Neustartversuchen; maximal 3 |

**Hinweis zu Berechtigungen:** Der Plugin ruft `systemctl restart zigbee2mqtt.service` auf, wenn die konfigurierte Neustartbedingung erfüllt ist. Der Domoticz-Prozess benötigt dafür passende Systemrechte. Vergib nur die dafür nötigen Rechte.

## Status- und Benachrichtigungslogik

- Geräte werden aus der Zigbee2MQTT-Geräteliste ermittelt.
- Die Offline-Erkennung basiert auf dem Alter des letzten `last_seen`-Zeitstempels und dem für den Gerätetyp gesetzten Timeout.
- Neue Offline-Geräte werden in einer Statusmeldung zusammen mit allen aktuell offline befindlichen Geräten aufgeführt, gruppiert nach Sensoren und Repeatern.
- Wieder erreichbare Geräte werden als wiederhergestellt gemeldet.
- Anhaltende Offline-Zustände können Erinnerungen auslösen; Statusinformationen werden in lokalen JSON-Dateien im Plugin-Verzeichnis gespeichert.
- Die automatische Neustartlogik ist von den Telegram-Meldungen getrennt. Ihre Bedingungen und Wartezeiten werden über die Parameter gesteuert.

Das konkrete Verhalten sollte nach der Installation mit einem kontrollierten Test geprüft werden, insbesondere Telegram-Versand, Wiederherstellung, Erinnerungsintervalle und Systemd-Berechtigungen.

## Persistente Dateien

Der Plugin legt Statusdateien neben `plugin.py` ab, unter anderem:

- `zigbee2mqtt_last_seen.json`
- `zigbee2mqtt_offline_state.json`
- `zigbee2mqtt_alert_state.json`

Diese Dateien enthalten den Laufzeitstatus für Zeitstempel und Benachrichtigungen. Lösche sie nicht während einer aktiven Störung, wenn du den bisherigen Zustand beibehalten möchtest. Sie enthalten keine MQTT-Zugangsdaten aus den hier dokumentierten Standardwerten.

## Fehlerbehebung

### Es kommen keine Telegram-Meldungen an
- Prüfe zuerst, ob Telegram-Benachrichtigungen in Domoticz selbst funktionieren.
- Prüfe das Domoticz-Protokoll auf `Telegram-Versand fehlgeschlagen`.
- Kontrolliere die Domoticz-URL und ob der Plugin Domoticz über diese URL erreichen kann.

### Geräte werden nicht erkannt
- Prüfe, ob Zigbee2MQTT `zigbee2mqtt/bridge/devices` veröffentlicht.
- Prüfe das MQTT-Basistopic.
- Prüfe, ob für die Geräte `last_seen` verfügbar ist und in der Zigbee2MQTT-Konfiguration aktualisiert wird.

### Neustart von Zigbee2MQTT schlägt fehl
- Prüfe das Domoticz-Protokoll auf Fehler von `systemctl`.
- Prüfe den Systemd-Dienstnamen und die Berechtigungen des Domoticz-Prozesses.
- Vermeide pauschale Root-Rechte; verwende gezielte Berechtigungen für den benötigten Dienst.

## Sicherheit und Betrieb

- MQTT-Benutzername und -Passwort werden in dieser Version nicht als eigene Plugin-Parameter angeboten. Schütze deshalb den MQTT-Broker mit geeigneten Netzwerk- und Zugriffseinstellungen.
- Die Domoticz-API-URL sollte auf den tatsächlich erreichbaren lokalen Endpunkt zeigen.
- Der automatische Neustart verändert den Zustand des Zigbee2MQTT-Dienstes. Teste diese Funktion zuerst in einer kontrollierten Umgebung.
- Der Plugin wurde syntaktisch geprüft; eine vollständige Integrationstestumgebung mit Domoticz, MQTT und Telegram ist für dieses Repository nicht enthalten.

## Entwicklung und Tests

Syntaxprüfung:

```bash
python3 -m py_compile plugin.py
```

Diese Prüfung bestätigt nur, dass Python den Quelltext kompilieren kann. Sie ersetzt keinen Funktionstest in Domoticz.

## Versionierung

Siehe [CHANGELOG.md](CHANGELOG.md).

## Fehler melden

Bitte füge bei einem Fehler hinzu:
- Domoticz-Version und Betriebssystem
- Zigbee2MQTT-Version
- relevante `[Zigbee-Watchdog]`-Protokollzeilen
- aktuelle Plugin-Parameter (Passwörter und andere Geheimnisse vorher entfernen)
- genaue Schritte, mit denen sich der Fehler reproduzieren lässt

## Lizenz

MIT. Siehe [LICENSE](LICENSE).
