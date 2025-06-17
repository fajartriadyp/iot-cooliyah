import time
import os
import threading
import logging
import json
import paho.mqtt.publish as publish
from gpiozero import OutputDevice, InputDevice, DistanceSensor
import blynklib
from gtts import gTTS

# ==============================================================================
# KONFIGURASI
# ==============================================================================
CONFIG = {
    "BLYNK_AUTH_TOKEN": "2sVX_BUqgS0qCuXVMliTC1RRPEXxWajF",
    "RELAY_PIN": 27,
    "IR_SENSOR_PIN": 17,
    "ULTRASONIC_ECHO_PIN": 24,
    "ULTRASONIC_TRIG_PIN": 23,
    "VALID_USERS": {
        "1668106708": "Ajay",
        "0987654321": "Budi",
    },
    "DETECTION_DISTANCE_CM": 50,
    "UNLOCK_DURATION_S": 5,
    "DOORWAY_CLEAR_CM": 30,
    "TTS_LANGUAGE": 'id',
    "MQTT_BROKER": "localhost",
    "MQTT_TOPIC": "smartdoor/logs"
}

# Logging Setup
logging.basicConfig(
    filename='door_system.log',
    level=logging.INFO,
    format='%(asctime)s %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

class SmartDoorLockSystem:
    def __init__(self, config):
        self.config = config
        self.is_locked = True
        self.person_detected_at_entrance = False
        self.waiting_for_rfid = False
        self.rfid_user_request = None
        self.blynk_unlock_request = False
        self.inside_sensor_request = False

        logging.info("Menginisialisasi Smart Door Lock System...")

        self.solenoid_relay = OutputDevice(self.config["RELAY_PIN"])
        self.inside_no_touch_sensor = InputDevice(self.config["IR_SENSOR_PIN"], pull_up=True)
        self.entrance_ultrasonic = DistanceSensor(
            echo=self.config["ULTRASONIC_ECHO_PIN"],
            trigger=self.config["ULTRASONIC_TRIG_PIN"]
        )

        if not os.path.exists('tts_cache'):
            os.makedirs('tts_cache')

        self.blynk = blynklib.Blynk(self.config['BLYNK_AUTH_TOKEN'])
        self.setup_blynk_handlers()

        threading.Thread(target=self.blynk.run, daemon=True).start()
        threading.Thread(target=self._rfid_reader_thread, daemon=True).start()
        threading.Thread(target=self._monitor_inside_sensor, daemon=True).start()

    def publish_log(self, message):
        try:
            payload = json.dumps({"message": message, "timestamp": time.time()})
            publish.single(self.config['MQTT_TOPIC'], payload, hostname=self.config['MQTT_BROKER'])
        except Exception as e:
            logging.error(f"MQTT publish error: {e}")

    def _monitor_inside_sensor(self):
        while True:
            try:
                if self.inside_no_touch_sensor.is_active and self.is_locked:
                    msg = "Sensor no-touch di dalam terdeteksi."
                    logging.info(msg)
                    self.publish_log(msg)
                    self.inside_sensor_request = True
                    time.sleep(2)
                time.sleep(0.1)
            except Exception as e:
                logging.error(f"Error monitoring inside sensor: {e}")
                time.sleep(1)

    def setup_blynk_handlers(self):
        @self.blynk.handle_event('write V1')
        def unlock_button_handler(pin, value):
            if value[0] == '1':
                msg = "Permintaan buka kunci dari aplikasi Blynk."
                logging.info(msg)
                self.publish_log(msg)
                self.blynk_unlock_request = True

    def speak(self, text):
        try:
            logging.info(f"TTS: {text}")
            filename = os.path.join('tts_cache', f'{hash(text)}.mp3')
            if not os.path.exists(filename):
                tts = gTTS(text=text, lang=self.config['TTS_LANGUAGE'], slow=False)
                tts.save(filename)
            os.system(f'mpg123 -q "{filename}/"')
        except Exception as e:
            logging.error(f"Error TTS: {e}")

    def _rfid_reader_thread(self):
        while True:
            try:
                print("\nSistem siap, silahkan tap kartu di pintu masuk...")
                card_uid = input().strip()
                if card_uid:
                    self._process_rfid_card(card_uid)
            except Exception as e:
                time.sleep(1)

    def _process_rfid_card(self, card_uid):
        user_name = self.config["VALID_USERS"].get(card_uid)
        if user_name and self.waiting_for_rfid:
            msg = f"RFID Valid: {card_uid} - {user_name}"
            logging.info(msg)
            self.publish_log(msg)
            self.rfid_user_request = user_name
            self.waiting_for_rfid = False
        elif user_name:
            msg = f"RFID Valid: {user_name}, tetapi sistem tidak sedang menunggu."
            logging.warning(msg)
            self.publish_log(msg)
        else:
            msg = f"RFID Ditolak: {card_uid} tidak terdaftar."
            logging.warning(msg)
            self.publish_log(msg)
            if self.waiting_for_rfid:
                self.speak("Kartu tidak terdaftar. Akses ditolak.")

    def update_blynk_status(self):
        try:
            status = "TERBUKA" if not self.is_locked else "TERKUNCI"
            self.blynk.virtual_write(2, status)
            led_value = 255 if not self.is_locked else 0
            self.blynk.virtual_write(3, led_value)
        except Exception as e:
            logging.error(f"Error updating Blynk: {e}")

    def unlock_door(self, method="UNKNOWN", user_name=None):
        if not self.is_locked: return
        msg = f"MEMBUKA KUNCI PINTU via {method}"
        logging.info(msg)
        self.publish_log(msg)
        self.solenoid_relay.on()
        self.is_locked = False
        self.update_blynk_status()
        if method == "RFID" and user_name:
            self.speak(f"Selamat datang, {user_name}")
        elif method == "INSIDE_SENSOR":
            self.speak("Pintu dibuka dari dalam.")
        elif method == "BLYNK":
            self.speak("Pintu dibuka dari aplikasi.")

    def lock_door(self):
        if self.is_locked: return
        msg = "MENGUNCI PINTU"
        logging.info(msg)
        self.publish_log(msg)
        self.solenoid_relay.off()
        self.is_locked = True
        self.update_blynk_status()
        self.person_detected_at_entrance = False
        self.waiting_for_rfid = False

    def monitor_entrance(self):
        try:
            distance = self.entrance_ultrasonic.distance * 100
            if distance < self.config['DETECTION_DISTANCE_CM'] and not self.person_detected_at_entrance and self.is_locked:
                msg = f"Orang terdeteksi di pintu masuk (jarak: {distance:.1f}cm)"
                logging.info(msg)
                self.publish_log(msg)
                self.person_detected_at_entrance = True
                self.waiting_for_rfid = True
                self.speak("Selamat datang, silahkan tap kartu Anda.")
            elif distance > self.config['DETECTION_DISTANCE_CM'] + 10:
                if self.person_detected_at_entrance and self.is_locked:
                    msg = "Orang menjauh dari pintu masuk."
                    logging.info(msg)
                    self.publish_log(msg)
                    self.person_detected_at_entrance = False
                    self.waiting_for_rfid = False
        except Exception as e:
            logging.error(f"Error monitoring entrance: {e}")

    def check_door_clear_for_locking(self):
        try:
            distance = self.entrance_ultrasonic.distance * 100
            while distance < self.config['DOORWAY_CLEAR_CM']:
                msg = f"Halangan terdeteksi: {distance:.1f}cm. Menunggu..."
                logging.warning(msg)
                self.publish_log(msg)
                self.speak("Pintu terhalang, penguncian ditunda.")
                time.sleep(2)
                distance = self.entrance_ultrasonic.distance * 100
            msg = f"Area pintu bersih: {distance:.1f}cm"
            logging.info(msg)
            self.publish_log(msg)
            return True
        except Exception as e:
            logging.error(f"Error checking door clearance: {e}")
            return True

    def run(self):
        self.lock_door()
        logging.info("SMART DOOR LOCK SYSTEM V3 - AKTIF")
        try:
            while True:
                self.monitor_entrance()
                if self.rfid_user_request and self.is_locked:
                    self.unlock_door("RFID", user_name=self.rfid_user_request)
                    self.rfid_user_request = None
                elif self.inside_sensor_request and self.is_locked:
                    self.unlock_door("INSIDE_SENSOR")
                    self.inside_sensor_request = False
                elif self.blynk_unlock_request and self.is_locked:
                    self.unlock_door("BLYNK")
                    self.blynk_unlock_request = False

                if not self.is_locked:
                    logging.info(f"Auto-lock dalam {self.config['UNLOCK_DURATION_S']} detik...")
                    time.sleep(self.config['UNLOCK_DURATION_S'])
                    logging.info("Memeriksa area pintu sebelum mengunci...")
                    if self.check_door_clear_for_locking():
                        self.lock_door()

                time.sleep(0.1)
        except KeyboardInterrupt:
            logging.info("Sistem dihentikan oleh user.")
        finally:
            self.lock_door()
            logging.info("Smart Door Lock System dimatikan.")

if __name__ == '__main__':
    door_system = SmartDoorLockSystem(CONFIG)
    door_system.run()
