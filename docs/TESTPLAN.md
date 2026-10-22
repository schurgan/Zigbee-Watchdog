# Testplan

Vor dem produktiven Einsatz in Domoticz prüfen:

- [ ] Plugin startet und verbindet sich mit MQTT.
- [ ] Geräteliste aus `bridge/devices` wird geladen.
- [ ] Health-Device zeigt `OK | Sens.:0/N | Rep.:0/M`, wenn keine Geräte offline sind.
- [ ] Ein Test-Sensor überschreitet seinen konfigurierten Timeout und wird gemeldet.
- [ ] Ein Test-Repeater überschreitet seinen konfigurierten Timeout und wird getrennt aufgeführt.
- [ ] Neue Offline-Geräte werden zusammen mit den bereits offline befindlichen Geräten aufgeführt.
- [ ] Wieder erreichbare Geräte erzeugen genau eine Wiederherstellungsmeldung.
- [ ] Erinnerungen kommen zu den erwarteten Zeitpunkten.
- [ ] Erinnerungszustand bleibt nach einem Plugin-Neustart konsistent.
- [ ] Neustart von `zigbee2mqtt.service` funktioniert nur bei erfüllter Bedingung und mit begrenzter Versuchszahl.
- [ ] Telegram-Formatierung wird auf dem tatsächlichen Zielgerät geprüft.
