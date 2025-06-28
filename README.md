# Python Backdoor Project

⚠️ **Disclaimer**: This project is intended strictly for educational and authorized penetration testing purposes. Unauthorized access or use of this software is illegal and unethical.

## 🎯 Project Overview

This Python-based backdoor enables remote access to a target system and provides the following core features:

### 🔑 Keylogger
Captures all keystrokes from the target machine and sends them back to the server in real time. Logged keys are also written to a file (`keylog.txt`) for further analysis.

### 🔼 Privilege Elevation
Attempts to escalate privileges using `pkexec`, allowing potential access to restricted commands or files. This simulates how attackers can gain administrative access if misconfigurations exist.

### 🖥️ Desktop & 🎙️ Audio Recording
- **Screen streaming**: Captures the target’s desktop continuously and streams it live to the controller.
- **Audio streaming**: Records microphone input and plays it live on the controller machine.
