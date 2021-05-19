# -*- coding: utf-8 -*-
from __future__ import absolute_import, unicode_literals

import threading
import octoprint.plugin
from octoprint.events import Events
import flask
import requests

try:
    import RPi.GPIO as GPIO
    GPIO_OK = True
except:
    GPIO_OK = False

if GPIO_OK:
    GPIO.setmode(GPIO.BOARD)
    GPIO.setwarnings(False)

class OctoLightHAPlugin(
        octoprint.plugin.AssetPlugin,
        octoprint.plugin.StartupPlugin,
        octoprint.plugin.TemplatePlugin,
        octoprint.plugin.SimpleApiPlugin,
        octoprint.plugin.SettingsPlugin,
        octoprint.plugin.EventHandlerPlugin,
        octoprint.plugin.RestartNeedingPlugin
    ):

    def __init__(self):
        self._check_light_state_thread = None
        self._check_light_state_event = threading.Event()
        self._light_state = False

    def get_settings_defaults(self):
        return dict(
            light_pin = 13,
            inverted_output = False,
            light_device = "GPIO"
        )

    def get_template_configs(self):
        return [
            dict(type="navbar", custom_bindings=True),
            dict(type="settings", custom_bindings=True)
        ]

    def get_assets(self):
        # Define your plugin's asset files to automatically include in the
        # core UI here.
        return dict(
            js=["js/octolightha.js"],
            css=["css/octolightha.css"],
            #less=["less/octolightha.less"]
        )

    def register_custom_events(self):
        return ["light_state_changed"]

    def on_after_startup(self):
        self._light_state = False
        self._logger.info("--------------------------------------------")
        self._logger.info("OctoLight HA started, listening for GET request")
        self._logger.info("Light pin: {}, inverted_input: {}".format(
            self._settings.get(["light_pin"]),
            self._settings.get(["inverted_output"])
        ))
        self._logger.info("--------------------------------------------")

        # Setting the default state of pin
        if GPIO_OK:
              GPIO.setup(int(self._settings.get(["light_pin"])), GPIO.OUT)
              if bool(self._settings.get(["inverted_output"])):
                GPIO.output(int(self._settings.get(["light_pin"])), GPIO.HIGH)
              else:
                GPIO.output(int(self._settings.get(["light_pin"])), GPIO.LOW)

        self._plugin_manager.send_plugin_message(self._identifier, dict(isLightOn=self._light_state))

        self._check_light_state_thread = threading.Thread(target=self._check_light_state)
        self._check_light_state_thread.daemon = True
        self._check_light_state_thread.start()

    def _gpio_toggle(self, light_state):
        GPIO.setup(int(self._settings.get(["light_pin"])), GPIO.OUT)
        if light_state ^ self._settings.get(["inverted_output"]):
            GPIO.output(int(self._settings.get(["light_pin"])), GPIO.HIGH)
        else:
            GPIO.output(int(self._settings.get(["light_pin"])), GPIO.LOW)

    def _ha_toggle(self, light_state):
        if light_state:
            self.turn_light_on()
        else:
            self.turn_light_off()

    def on_api_get(self, request):
        self._light_state = not self._light_state

        light_device = self._settings.get(["light_device"])
        if light_device == "GPIO" and GPIO_OK:
            self._gpio_toggle(self._light_state)

        if light_device == "HA":
            self._ha_toggle(self._light_state)

        self._logger.info("Got request. Light state: {}".format(
            self._light_state
        ))

        self._plugin_manager.send_plugin_message(self._identifier, dict(isLightOn=self._light_state))

        return flask.jsonify(status="ok")

    def on_event(self, event, payload):
        if event == Events.CLIENT_OPENED:
            self._plugin_manager.send_plugin_message(self._identifier, dict(isLightOn=self._light_state))
            return

    def get_update_information(self):
        return dict(
            octolightha=dict(
                displayName="OctoLight HomeAssistant",
                displayVersion=self._plugin_version,

                type="github_release",
                current=self._plugin_version,

                user="emes30",
                repo="OctoLightHA",
                pip="https://github.com/emes30/OctoLightHA/archive/{target}.zip"
            )
        )

    def send(self, cmd, data=None):
        """
        Send request via Home Assistant API

        Args:
            cmd (str): command to execute
            data (str, optional): command arguments. Defaults to None.

        Returns:
            bool: request response object
        """
        url = self._settings.get(['address']) + '/api' + cmd

        headers = dict(Authorization='Bearer ' + self._settings.get(['api_key']))

        response = None
        verify_certificate = self._settings.get(['verify_certificate'])
        try:
            if data:
                response = requests.post(url, headers=headers, data=data, verify=verify_certificate)
            else:
                response = requests.get(url, headers=headers, verify=verify_certificate)
        except (
                requests.exceptions.InvalidURL,
                requests.exceptions.ConnectionError
        ):
            self._logger.error("Unable to communicate with server. Check settings.")
        except Exception:
            self._logger.exception("Exception while making API call")
        else:
            if data:
                self._logger.debug("cmd={}, data={}, status_code={}, text={}".format(cmd, data, response.status_code, response.text))
            else:
                self._logger.debug("cmd={}, status_code={}, text={}".format(cmd, response.status_code, response.text))

            if response.status_code == 401:
                self._logger.warning("Server returned 401 Unauthorized. Check API key.")
                response = None
            elif response.status_code == 404:
                self._logger.warning("Server returned 404 Not Found. Check Entity ID.")
                response = None

        return response

    def _entity_id(self):
        _entity_id = self._settings.get(['entity_id'])
        if _entity_id is None:
            return "", ""
        _domainsplit = _entity_id.find('.')
        if _domainsplit < 0:
            _domain = 'light'
            _entity_id = _domain + '.' + _entity_id
        else:
            _domain = _entity_id[:_domainsplit]
        _domain = "homeassistant"
        return _domain, _entity_id

    def change_light_state(self, state):
        _domain, _entity_id = self._entity_id()

        if state:
            cmd = '/services/' + _domain + '/turn_' + state
        else:
            cmd = '/services/' + _domain + '/toggle'
        data = '{"entity_id":"' + _entity_id + '"}'
        self.send(cmd, data)

    def turn_light_on(self):
        self._logger.debug("Turn the light on")
        self.change_light_state('on')

    def turn_light_off(self):
        self._logger.debug("Turn the light off")
        self.change_light_state('off')

    def get_light_state(self):
        _domain, _entity_id = self._entity_id()

        cmd = '/states/' + _entity_id

        response = self.send(cmd)
        if not response:
            return False
        data = response.json()

        status = None
        try:
            status = (data['state'] == 'on')
        except KeyError:
            pass

        if status == None:
            self._logger.error("Unable to determine status. Check settings.")
            status = False

        return status

    def _check_light_state(self):
        while True:
            old_state = self._light_state

            self._logger.debug("Polling light state...")

            if self._settings.get(["light_device"]) == 'GPIO' and GPIO_OK:
                r = False
                try:
                    r = GPIO.input(int(self._settings.get(["light_pin"]))) ^ self._settings.get(["inverted_output"])
                except Exception:
                    self._logger.exception("Exception while reading GPIO line")

                self._logger.debug("Result: {}".format(r))

                self._light_state = r

            if self._settings.get(["light_device"]) == 'HA':
                self._light_state = self.get_light_state()

            self._logger.debug("light_state: {}".format(self._light_state))

            if (old_state != self._light_state):
                self._logger.debug("Light state changed, firing psu_state_changed event.")

                event = Events.PLUGIN_OCTOLIGHTHA_LIGHT_STATE_CHANGED
                self._event_bus.fire(event, payload=dict(isLightOn=self._light_state))
                self._plugin_manager.send_plugin_message(self._identifier, dict(isLightOn=self._light_state))

            self._check_light_state_event.wait(5)
            self._check_light_state_event.clear()


__plugin_pythoncompat__ = ">=2.7,<4"
__plugin_implementation__ = OctoLightHAPlugin()

__plugin_hooks__ = {
    "octoprint.plugin.softwareupdate.check_config": __plugin_implementation__.get_update_information,
    "octoprint.events.register_custom_events": __plugin_implementation__.register_custom_events
}
