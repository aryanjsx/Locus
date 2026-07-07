# aura/utils/mic_lock.py
#
# Shared mutex for microphone device access.
#
# On single-mic systems, only one audio stream can be open at a time.
# Both WakeWordListener and STTEngine acquire this lock before opening
# a sounddevice InputStream, preventing garbled audio or device errors
# when both components are active (e.g., if wake-word is erroneously
# resumed during a SafetyGate confirmation recording).
#
# This is defense-in-depth — the primary serialization is event-based
# (SessionController pauses WakeWordListener during active sessions).

import threading

mic_lock = threading.Lock()
