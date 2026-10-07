"""Real Chromium UI checks against isolated state and mocked Pi operations.

Requires Debian chromium, chromium-driver and python3-selenium. No real Spotify,
Wi-Fi, power or USB operation is performed by this test.
"""
import json
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.chrome.service import Service

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pi'))
import config_server
from spotify_profiles import private_json

out = Path('/tmp/nano-config-browser')
out.mkdir(exist_ok=True)
calls = []


def service(action, name='go-librespot.service'):
    calls.append(('service', action, name))


def helper(action, **values):
    calls.append(('helper', action))
    return {'message': 'Test ' + action}


def player(path, payload=None):
    if path == '/auth/code':
        return {'url': 'https://accounts.spotify.com/pair', 'code': 'TEST-CODE', 'expires_at': '2099-01-01T12:00:00Z'}
    return {'username': 'test-account', 'playback_ready': True}


with tempfile.TemporaryDirectory() as temporary:
    directory = Path(temporary)
    manifest = directory / 'apps.toml'
    manifest.write_bytes(config_server.MANIFEST.read_bytes())
    private_json(directory / 'player/state.json', {'device_id': 'test-device', 'credentials': {'username': 'test-account', 'data': 'test-secret'}})
    with patch.object(config_server, 'PLAYER', directory / 'player'), patch.object(config_server, 'MANIFEST', manifest), \
            patch.object(config_server, 'user_service', service), patch.object(config_server, 'helper', helper), patch.object(config_server, 'player_api', player):
        application = config_server.Application(directory / 'config')
        application.status = dict(hostname='ltpi', addresses=[], url='http://192.168.1.120:8080', temperature=43.2,
                                  uptime=86400, nano_connected=True, controls_ready=True,
                                  spotify={'ready': True, 'username': 'test-account', 'track': 'Test track'},
                                  services={'go-librespot.service': 'active', 'ipod-spotify-bridge.service': 'active'})
        application.wifi = {'interfaces': ['wlan0'], 'confirmation_pending': False,
                            'networks': [{'ssid': 'House WiFi', 'interface': 'wlan0', 'signal': 80, 'security': 'WPA2', 'active': True}],
                            'saved': [{'name': 'House WiFi', 'uuid': '11111111-1111-1111-1111-111111111111'}]}
        application.bluetooth = {'flags': 17, 'message': 'Speaker connected', 'devices': [{'name': 'WONDERBOOM', 'address': 'C0:28:8D:72:38:C2', 'flags': 7}]}
        application.jobs.installer = lambda reload_only: calls.append(('installer', reload_only)) or {'message': 'Apps reloaded'}
        server = config_server.Server(('127.0.0.1', 0), config_server.Handler)
        server.application = application
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        options = webdriver.ChromeOptions()
        options.binary_location = '/usr/bin/chromium'
        for argument in ('--headless=new', '--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu', '--window-size=1200,1100'):
            options.add_argument(argument)
        options.set_capability('goog:loggingPrefs', {'browser': 'ALL'})
        driver = webdriver.Chrome(service=Service('/usr/bin/chromedriver'), options=options)
        wait = WebDriverWait(driver, 15)

        def element(selector):
            return driver.find_element(By.CSS_SELECTOR, selector)

        def refresh():
            driver.execute_script('refresh()')

        def tab(name):
            element('[data-tab="' + name + '"]').click()

        def no_overflow():
            assert driver.execute_script('return document.documentElement.scrollWidth <= document.documentElement.clientWidth'), 'Page overflows horizontally'

        try:
            base = 'http://127.0.0.1:' + str(server.server_port)
            driver.get(base)
            wait.until(lambda _: element('#temperature').text == '43.2 °C')
            assert element('#reload-home').get_attribute('disabled') is not None
            driver.save_screenshot(str(out / 'overview-desktop.png'))
            driver.get(base + '/#setup=' + application.auth.setup_token)
            wait.until(lambda _: element('#login-dialog').get_attribute('open') is not None)
            element('#login-password').send_keys('browser-test-password')
            element('#confirm-password').send_keys('browser-test-password')
            element('#login-submit').click()
            wait.until(lambda _: element('#sign-out').is_displayed())
            assert not application.auth.setup_token
            assert 'setup=' not in driver.current_url
            tab('profiles')
            wait.until(lambda _: len(driver.find_elements(By.CSS_SELECTOR, '.profile')) == 1)
            assert element('.profile').text.find('Active') >= 0
            driver.save_screenshot(str(out / 'profiles-desktop.png'))
            element('#add-profile').click()
            driver.switch_to.alert.send_keys('Roommate')
            driver.switch_to.alert.accept()
            driver.switch_to.alert.accept()
            wait.until(lambda _: application.profiles.public()['pending'])
            refresh()
            wait.until(lambda _: element('#spotify-link').is_displayed())
            assert element('#pair-code').text == 'TEST-CODE'
            element('#cancel-pairing').click()
            wait.until(lambda _: not application.profiles.public()['pending'])
            refresh()
            tab('apps')
            wait.until(lambda _: len(driver.find_elements(By.CSS_SELECTOR, '#app-grid input')) > 30)
            element('#app-grid input[value="clock"]').click()
            element('#save-apps').click()
            wait.until(lambda _: 'clock = true' in manifest.read_text())
            refresh()
            wait.until(lambda _: not element('#reload-apps').get_attribute('disabled'))
            element('#reload-apps').click()
            driver.switch_to.alert.accept()
            wait.until(lambda _: ('installer', True) in calls)
            refresh()
            tab('wifi')
            wait.until(lambda _: len(driver.find_elements(By.CSS_SELECTOR, '#wifi-networks .device')) == 1)
            element('#wifi-networks button').click()
            assert element('#wifi-ssid').get_attribute('value') == 'House WiFi'
            element('#wifi-password').send_keys('wifi-test-only')
            element('#wifi-form button').click()
            driver.switch_to.alert.dismiss()
            assert ('helper', 'wifi-connect') not in calls
            tab('system')
            element('#poweroff').click()
            driver.switch_to.alert.dismiss()
            assert ('helper', 'poweroff') not in calls
            driver.set_window_size(390, 900)
            for name in ('overview', 'profiles', 'apps', 'bluetooth', 'wifi', 'system'):
                tab(name)
                no_overflow()
                driver.save_screenshot(str(out / (name + '-mobile.png')))
            element('#sign-out').click()
            wait.until(lambda _: element('#sign-in').is_displayed())
            element('#sign-in').click()
            element('#login-password').send_keys('wrong-password')
            element('#login-submit').click()
            wait.until(lambda _: 'Incorrect' in element('#login-error').text)
            element('#login-password').clear()
            element('#login-password').send_keys('browser-test-password')
            element('#login-submit').click()
            wait.until(lambda _: element('#sign-out').is_displayed())
            errors = [e for e in driver.get_log('browser') if e['level'] == 'SEVERE' and '401' not in e['message'] and 'favicon' not in e['message']]
            assert not errors, errors
            print('Browser passed: setup, password/login/logout, profiles/login cancellation, app selection/reload, Wi-Fi and power confirmation, all six mobile layouts')
            print('Screenshots:', out)
        finally:
            driver.quit()
            server.shutdown()
            server.server_close()
