import time
import os
import threading
import serial
from gpiozero import OutputDevice, InputDevice, DistanceSensor
import blynklib
import pygame

# ==============================================================================
# KONFIGURASI - SILAKAN UBAH BAGIAN INI SESUAI KEBUTUHAN ANDA
# ==============================================================================
CONFIG = {
    # --- KONEKSI BLYNK ---
    "BLYNK_TEMPLATE_ID": "TMPL6PVypMfhN",
    "BLYNK_TEMPLATE_NAME": "smartdoor", 
    "BLYNK_AUTH_TOKEN": "9H9FQqEVAm7A0SAuNuPxqSXJwbzc9Wp5",

    # --- Pin GPIO ---
    "RELAY_PIN": 27,
    "IR_SENSOR_PIN": 17,          # Sensor no-touch di dalam ruangan
    "ULTRASONIC_ECHO_PIN": 24,    # Sensor jarak di pintu masuk
    "ULTRASONIC_TRIG_PIN": 23,
    
    # --- RFID Reader ---
    "RFID_SERIAL_PORT": "/dev/ttyUSB0",  # Port serial untuk RFID reader
    "RFID_BAUDRATE": 9600,

    # --- RFID Users Database ---
    # Format: "UID_KARTU": "NAMA_PENGGUNA"
    "VALID_USERS": {
        "1234567890": "Ajay",
        "0987654321": "Budi", 
        "1122334455": "Sari",
        "2727983226": "Bilal"
    },

    # --- Pengaturan Sistem ---
    "DETECTION_DISTANCE_CM": 50,  # Jarak deteksi orang di pintu masuk
    "UNLOCK_DURATION_S": 5,       # Durasi pintu terbuka (detik)
    "DOORWAY_CLEAR_CM": 30,       # Jarak minimum untuk penguncian otomatis
    
    # --- File Audio ---
    "AUDIO_FILES": {
        "welcome": "silahkan.mp3",
        "enter": "masuk.mp3", 
        "denied": "maaf.mp3"
    }
}
# ==============================================================================

class SmartDoorLockSystem:
    def __init__(self, config):
        self.config = config
        
        # Status sistem
        self.is_locked = True
        self.person_detected_at_entrance = False
        self.waiting_for_rfid = False
        self.rfid_timeout = 10  # timeout untuk menunggu RFID (detik)
        
        # Flags untuk permintaan unlock
        self.rfid_unlock_approved = False
        self.rfid_user_name = None
        self.inside_sensor_unlock = False
        self.blynk_unlock_request = False
        
        # Thread control
        self.running = True
        self.lock = threading.Lock()
        
        print("🔐 Menginisialisasi Smart Door Lock System...")
        
        # Inisialisasi Hardware
        self._init_hardware()
        
        # Inisialisasi Audio
        self._init_audio()
        
        # Inisialisasi Blynk
        self._init_blynk()
        
        # Start background threads
        self._start_threads()
        
        print("✅ Smart Door Lock System siap digunakan!")

    def _init_hardware(self):
        """Inisialisasi semua hardware."""
        try:
            # Solenoid relay untuk door lock
            self.solenoid_relay = OutputDevice(self.config["RELAY_PIN"])
            
            # Sensor no-touch di dalam
            self.inside_no_touch_sensor = InputDevice(
                self.config["IR_SENSOR_PIN"], 
                pull_up=True
            )
            
            # Ultrasonic sensor di pintu masuk
            self.entrance_ultrasonic = DistanceSensor(
                echo=self.config["ULTRASONIC_ECHO_PIN"],
                trigger=self.config["ULTRASONIC_TRIG_PIN"]
            )
            
            # RFID Reader Serial
            try:
                self.rfid_serial = serial.Serial(
                    port=self.config["RFID_SERIAL_PORT"],
                    baudrate=self.config["RFID_BAUDRATE"],
                    timeout=0.1
                )
                print(f"📡 RFID Reader terhubung di {self.config['RFID_SERIAL_PORT']}")
            except Exception as e:
                print(f"⚠️  RFID Reader error: {e}")
                print("📝 Menggunakan mode input manual untuk testing")
                self.rfid_serial = None
                
            print("🔧 Hardware berhasil diinisialisasi")
            
        except Exception as e:
            print(f"❌ Error inisialisasi hardware: {e}")
            raise

    def _init_audio(self):
        """Inisialisasi sistem audio."""
        try:
            pygame.mixer.init()
            
            # Cek apakah file audio tersedia
            audio_dir = "audio"
            if not os.path.exists(audio_dir):
                os.makedirs(audio_dir)
                print(f"📁 Folder '{audio_dir}' dibuat")
            
            missing_files = []
            for key, filename in self.config["AUDIO_FILES"].items():
                filepath = os.path.join(audio_dir, filename)
                if not os.path.exists(filepath):
                    missing_files.append(filename)
            
            if missing_files:
                print(f"⚠️  File audio tidak ditemukan: {missing_files}")
                print("📝 Pastikan file audio tersedia di folder 'audio/'")
            else:
                print("🔊 Sistem audio siap")
                
        except Exception as e:
            print(f"❌ Error inisialisasi audio: {e}")

    def _init_blynk(self):
        """Inisialisasi koneksi Blynk."""
        try:
            self.blynk = blynklib.Blynk(self.config['BLYNK_AUTH_TOKEN'])
            self._setup_blynk_handlers()
            print("☁️  Blynk siap terhubung")
        except Exception as e:
            print(f"❌ Error inisialisasi Blynk: {e}")

    def _setup_blynk_handlers(self):
        """Setup Blynk event handlers."""
        @self.blynk.handle_event('write V1')
        def unlock_button_handler(pin, value):
            if value[0] == '1':
                print("📱 Permintaan unlock dari Blynk app")
                with self.lock:
                    self.blynk_unlock_request = True

    def _start_threads(self):
        """Start semua background threads."""
        threads = [
            threading.Thread(target=self._blynk_thread, daemon=True),
            threading.Thread(target=self._rfid_reader_thread, daemon=True),
            threading.Thread(target=self._inside_sensor_thread, daemon=True),
            threading.Thread(target=self._entrance_monitor_thread, daemon=True)
        ]
        
        for thread in threads:
            thread.start()
        
        print("🚀 Background threads started")

    def _blynk_thread(self):
        """Thread untuk koneksi Blynk."""
        while self.running:
            try:
                self.blynk.run()
            except Exception as e:
                print(f"❌ Blynk error: {e}")
                time.sleep(5)

    def _rfid_reader_thread(self):
        """Thread untuk membaca RFID."""
        print("🔍 RFID reader thread started")
        while self.running:
            try:
                if self.waiting_for_rfid:
                    print("⏳ Menunggu kartu RFID...")
                    card_uid = self._read_rfid_card()
                    if card_uid:
                        self._process_rfid_card(card_uid)
                
                time.sleep(0.5)  # Slower polling untuk debug
                
            except Exception as e:
                print(f"❌ RFID reader error: {e}")
                time.sleep(1)

    def _read_rfid_card(self):
        """Baca kartu RFID."""
        try:
            if self.rfid_serial and self.rfid_serial.in_waiting > 0:
                rfid_data = self.rfid_serial.readline().decode('utf-8').strip()
                if rfid_data:
                    print(f"📡 RFID data received: {rfid_data}")
                    return rfid_data.replace('\r', '').replace('\n', '')
            elif not self.rfid_serial:
                # Fallback untuk testing - simulasi dengan input manual
                print("💳 [TEST MODE] Simulasi RFID - masukkan UID atau 'skip':")
                # Untuk testing, kita bisa return test UID
                # return "1234567890"  # Uncomment untuk test otomatis
                pass
                
        except Exception as e:
            print(f"❌ Error reading RFID: {e}")
        
        return None

    def _process_rfid_card(self, card_uid):
        """Proses kartu RFID yang dibaca."""
        user_name = self.config["VALID_USERS"].get(card_uid)
        
        with self.lock:
            if user_name:
                print(f"✅ RFID Valid: {card_uid} - {user_name}")
                self.rfid_unlock_approved = True
                self.rfid_user_name = user_name
            else:
                print(f"❌ RFID Ditolak: {card_uid} tidak terdaftar")
                self.rfid_unlock_approved = False
                
            self.waiting_for_rfid = False

    def _inside_sensor_thread(self):
        """Thread untuk monitor sensor di dalam."""
        print("🔍 Inside sensor thread started")
        while self.running:
            try:
                # Debug sensor state
                sensor_state = self.inside_no_touch_sensor.is_active
                
                # Sensor no-touch (active low - False berarti terdeteksi)
                if not sensor_state and self.is_locked:
                    print("👆 Sensor no-touch di dalam terdeteksi")
                    with self.lock:
                        self.inside_sensor_unlock = True
                    time.sleep(2)  # Debounce
                    
                time.sleep(0.5)  # Slower polling untuk debug
                
            except Exception as e:
                print(f"❌ Inside sensor error: {e}")
                time.sleep(1)

    def _entrance_monitor_thread(self):
        """Thread untuk monitor pintu masuk."""
        print("🔍 Entrance monitor thread started")
        rfid_start_time = None
        
        while self.running:
            try:
                distance = self.entrance_ultrasonic.distance * 100
                
                # Debug output setiap 5 detik
                if int(time.time()) % 5 == 0:
                    print(f"📏 Jarak saat ini: {distance:.1f}cm | Locked: {self.is_locked}")
                
                # Deteksi orang di pintu masuk
                if (distance < self.config['DETECTION_DISTANCE_CM'] and 
                    not self.person_detected_at_entrance and 
                    self.is_locked):
                    
                    print(f"👤 Orang terdeteksi di pintu masuk (jarak: {distance:.1f}cm)")
                    
                    with self.lock:
                        self.person_detected_at_entrance = True
                        self.waiting_for_rfid = True
                        
                    rfid_start_time = time.time()
                    self.play_audio("welcome")  # "Silahkan tap kartu anda"
                
                # Timeout RFID
                elif (self.waiting_for_rfid and rfid_start_time and 
                      time.time() - rfid_start_time > self.rfid_timeout):
                    
                    print("⏰ RFID timeout - reset detection")
                    with self.lock:
                        self.waiting_for_rfid = False
                        self.person_detected_at_entrance = False
                    rfid_start_time = None
                
                # Reset detection jika orang menjauh
                elif distance > self.config['DETECTION_DISTANCE_CM'] + 10:
                    if self.person_detected_at_entrance and self.is_locked:
                        print("🚶 Orang menjauh dari pintu masuk")
                        with self.lock:
                            self.person_detected_at_entrance = False
                            self.waiting_for_rfid = False
                        rfid_start_time = None
                
                time.sleep(1.0)  # Slower polling untuk debug
                
            except Exception as e:
                print(f"❌ Entrance monitor error: {e}")
                time.sleep(1)

    def play_audio(self, audio_key):
        """Putar file audio."""
        try:
            filename = self.config["AUDIO_FILES"].get(audio_key)
            if filename:
                filepath = os.path.join("audio", filename)
                if os.path.exists(filepath):
                    pygame.mixer.music.load(filepath)
                    pygame.mixer.music.play()
                    print(f"🔊 Playing: {filename}")
                else:
                    print(f"⚠️  File audio tidak ditemukan: {filepath}")
            
        except Exception as e:
            print(f"❌ Audio error: {e}")

    def unlock_door(self, method="UNKNOWN", user_name=None):
        """Buka kunci pintu."""
        if not self.is_locked:
            return
            
        print(f"🔓 MEMBUKA PINTU via {method}")
        
        with self.lock:
            self.solenoid_relay.on()  # Aktifkan relay (buka kunci)
            self.is_locked = False
        
        # Update status ke Blynk
        self._update_blynk_status()
        
        # Putar audio berdasarkan metode
        if method == "RFID" and user_name:
            print(f"👋 Selamat datang, {user_name}")
            self.play_audio("enter")  # "Silahkan masuk"
        elif method == "INSIDE_SENSOR":
            print("🏠 Pintu dibuka dari dalam")
            self.play_audio("enter")
        elif method == "BLYNK":
            print("📱 Pintu dibuka dari aplikasi")
            self.play_audio("enter")

    def lock_door(self):
        """Kunci pintu."""
        if self.is_locked:
            return
            
        print("🔒 MENGUNCI PINTU")
        
        with self.lock:
            self.solenoid_relay.off()  # Matikan relay (kunci pintu)
            self.is_locked = True
            
            # Reset flags
            self.person_detected_at_entrance = False
            self.waiting_for_rfid = False
            self.rfid_unlock_approved = False
            self.rfid_user_name = None
        
        # Update status ke Blynk
        self._update_blynk_status()

    def _update_blynk_status(self):
        """Update status ke Blynk app."""
        try:
            status = "TERBUKA" if not self.is_locked else "TERKUNCI"
            self.blynk.virtual_write(2, status)
            
            # LED indicator
            led_value = 255 if not self.is_locked else 0
            self.blynk.virtual_write(3, led_value)
            
        except Exception as e:
            print(f"❌ Blynk update error: {e}")

    def _check_door_clear_for_locking(self):
        """Cek apakah area pintu bersih untuk penguncian."""
        try:
            distance = self.entrance_ultrasonic.distance * 100
            
            if distance < self.config['DOORWAY_CLEAR_CM']:
                print(f"⚠️  Pintu terhalang ({distance:.1f}cm), penguncian ditunda")
                return False
            
            return True
            
        except Exception as e:
            print(f"❌ Door clearance check error: {e}")
            return True

    def run(self):
        """Main application loop."""
        # Pastikan pintu terkunci saat start
        self.lock_door()
        
        print("\n" + "="*70)
        print("🔐 SMART DOOR LOCK SYSTEM - AKTIF")
        print("Flow: Ultrasonic Detection → RFID Tap → Voice Welcome → Door Unlock")
        print("Akses: RFID Card | Inside No-Touch Sensor | Blynk App")
        print("="*70)
        print("🔄 Main loop started - sistem sedang berjalan...")

        try:
            loop_count = 0
            while self.running:
                loop_count += 1
                
                # Debug output setiap 10 detik
                if loop_count % 100 == 0:  # setiap 10 detik (0.1s * 100)
                    print(f"💓 Heartbeat #{loop_count//100} - System running normally")
                
                # Proses permintaan unlock
                with self.lock:
                    # RFID unlock
                    if self.rfid_unlock_approved and self.is_locked:
                        self.unlock_door("RFID", user_name=self.rfid_user_name)
                        self.rfid_unlock_approved = False
                        self.rfid_user_name = None
                        
                    # RFID ditolak
                    elif (not self.rfid_unlock_approved and 
                          not self.waiting_for_rfid and 
                          self.person_detected_at_entrance):
                        
                        print("❌ Akses ditolak")
                        self.play_audio("denied")  # "Maaf kartu belum terdaftar"
                        self.person_detected_at_entrance = False
                    
                    # Inside sensor unlock
                    elif self.inside_sensor_unlock and self.is_locked:
                        self.unlock_door("INSIDE_SENSOR")
                        self.inside_sensor_unlock = False
                    
                    # Blynk unlock
                    elif self.blynk_unlock_request and self.is_locked:
                        self.unlock_door("BLYNK")
                        self.blynk_unlock_request = False

                # Auto-lock setelah durasi tertentu
                if not self.is_locked:
                    print(f"⏳ Auto-lock dalam {self.config['UNLOCK_DURATION_S']} detik...")
                    time.sleep(self.config['UNLOCK_DURATION_S'])
                    
                    # Cek area pintu sebelum mengunci
                    if self._check_door_clear_for_locking():
                        self.lock_door()
                    else:
                        print("⏳ Menunggu area pintu bersih...")

                time.sleep(0.1)

        except KeyboardInterrupt:
            print("\n⏹️  Sistem dihentikan oleh user")
        except Exception as e:
            print(f"❌ Error sistem: {e}")
            import traceback
            traceback.print_exc()
        finally:
            self.running = False
            self.lock_door()
            print("🔐 Smart Door Lock System dimatikan")

# ==============================================================================
# MAIN PROGRAM
# ==============================================================================
if __name__ == '__main__':
    try:
        # Inisialisasi dan jalankan sistem
        door_system = SmartDoorLockSystem(CONFIG)
        door_system.run()
        
    except Exception as e:
        print(f"❌ Fatal error: {e}")
    finally:
        print("👋 Program selesai")