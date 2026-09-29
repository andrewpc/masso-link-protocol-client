#!/usr/bin/env python3
"""
MASSO UDP Client - Interactive Tool
Supports full connection sequence and status monitoring
"""

import os
import sys
import time
import threading
import socket
import struct
import json
from datetime import datetime


class MassoClient:
    """Interactive UDP client for MASSO lathe."""
    
    def __init__(self, host=None, debug=False):
        self.host = host
        self.debug = debug
        self.pc_port = 11000
        self.controller_port = 65535
        self.socket = None
        self.connected = False
        self.listening = False
        self.listen_thread = None
        self.keepalive_thread = None
        self.last_status = None
        self.tool_data = {}
        self.monitor_mode = False
        self.log_file = None
        self._last_tool_index = 1  # For paginated tool loading (1-based indexing)
        self._upload_ack_received = threading.Event()  # For synchronous upload
        self._last_ack_packet = None  # Stores last upload ACK payload
        self.job_count = None  # Latest job counter from status packets
        self._last_line_change_time = None
        self._last_line_value = None
        self._in_feed_hold = False
        self._feed_hold_threshold = 1.5  # seconds without line change while running
        self._watch_thread = None
        self._watch_stop_event = threading.Event()
        self._watch_directory = None
        self._watch_state_file = None  # Path to state file for tracking uploads
        self._packets_received = 0  # Counter for connection verification
        self._connect_time = None  # PC time snapshot sent to the controller (sets its clock)
        
    def _clock(self):
        """PC time snapshot used in packets; fixed at connect time like MASSO Link."""
        if self._connect_time is None:
            self._connect_time = datetime.now()
        return self._connect_time

    def _build_tool_request_packet(self, tool_index):
        """Build a tool data request packet for the given tool index."""
        if tool_index < 1 or tool_index > 255:
            raise ValueError("Tool index must be between 1 and 255")
        
        # Packet structure: [CRC16][Magic 0x03 0x00][Type 0x08][Tool Index][minute second day month]
        # Checksum is calculated over magic + type + index + payload (bytes 2+)
        # The 4 trailing bytes are the connect time (captures: 22 2c 1c 0b = 34:44 on 28 Nov)
        t = self._clock()
        payload = bytes([0x03, 0x00, 0x08, tool_index, t.minute, t.second, t.day, t.month])
        checksum = self._calculate_checksum(payload)
        return checksum + payload
    
    def _build_discovery_packet(self):
        """Build discovery packet."""
        # Last byte was the month in MASSO Link captures but the controller does not check it
        payload = bytes([0x03, 0x00, 0x02, 0xf8, 0x2a, 0x00, 0x00, self._clock().month])
        checksum = self._calculate_checksum(payload)
        return checksum + payload
    
    def _build_config_packet(self):
        """Build configuration packet.

        Carries the PC time; the controller sets its clock from it (all zeros
        resets the controller clock to 12:00 AM).
        """
        t = self._clock()
        payload = bytes([
            0x03, 0x00, 0x03,  # Magic + Type
            t.hour, t.minute, t.second, t.day, t.month, t.year % 100,
            0x00, 0x00, 0x00  # Unknown 3 bytes (always zero in captures)
        ])
        checksum = self._calculate_checksum(payload)
        return checksum + payload
    
    def _build_keepalive_packet(self):
        """Build keepalive packet."""
        t = self._clock()
        payload = bytes([
            0x03, 0x00, 0x01,  # Magic + Type
            t.hour, t.minute, t.second, t.day, t.month  # Connect time, as sent by MASSO Link
        ])
        checksum = self._calculate_checksum(payload)
        return checksum + payload
    
    def start(self):
        """Start the client, finding an available port."""
        for port in range(11000, 11051):
            try:
                self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self.socket.bind(('', port))
                self.pc_port = port
                print(f"[+] Bound to UDP port {self.pc_port}")
                
                self.listening = True
                self.listen_thread = threading.Thread(target=self._listen_loop, daemon=True)
                self.listen_thread.start()
                return True
            except OSError:
                # Port busy, try next
                self.socket.close()
                continue
            except Exception as e:
                print(f"[-] Error starting client on port {port}: {e}")
                return False
        
        print("[-] Could not find available port in range 11000-11050")
        return False
    
    def connect(self):
        """Perform connection handshake."""
        if not self.host:
            print("[-] No host IP specified. Use 'connect <IP>' command or --host argument")
            return False
        
        print(f"[+] Connecting to {self.host}...")
        self._connect_time = datetime.now()
        
        # 1. Discovery
        print("    Sending Discovery...")
        discovery_packet = self._build_discovery_packet()
        self.send_packet(discovery_packet)
        time.sleep(0.5)
        
        # 2. Config
        print("    Sending Config...")
        config_packet = self._build_config_packet()
        self.send_packet(config_packet)
        time.sleep(0.5)
        
        # Start keepalive thread
        if not self.keepalive_thread or not self.keepalive_thread.is_alive():
            self.connected = True
            self.keepalive_thread = threading.Thread(target=self._keepalive_loop, daemon=True)
            self.keepalive_thread.start()
            print("[+] Connection sequence complete. Keepalives started.")
            return True
        
        return False
            
    def get_tools(self, start_index=1, batch_size=10):
        """Request list of tools progressively until empty tool found."""
        if not self.connected:
            print("[-] Not connected")
            return
        
        # Check if we're receiving any data at all
        initial_packet_count = self._packets_received
        
        # Request tools in batches until we get an empty response
        requests_sent = 0
        batch_size = 10
        current_index = start_index
        
        while True:
            # Request next batch
            for idx in range(current_index, current_index + batch_size):
                try:
                    pkt = self._build_tool_request_packet(idx)
                    self.send_packet(pkt, silent=False, message=f"Requesting Tool {idx}")
                    requests_sent += 1
                except Exception as e:
                    print(f"[-] Error requesting tool {idx}: {e}")
            
            # Wait for responses
            time.sleep(2.0)  # Increased from 0.5 to 2.0 seconds
            
            # Check if we received any new tools in this batch
            batch_start = current_index
            batch_end = current_index + batch_size - 1
            new_tools_in_batch = any(i in self.tool_data for i in range(batch_start, batch_end + 1))
            
            # Check if we hit an empty tool
            found_empty = False
            for idx in range(batch_start, batch_end + 1):
                if idx in self.tool_data:
                    tool_name = self.tool_data[idx].strip()
                    if not tool_name or tool_name == '':
                        print(f"\n[+] Found empty tool at index {idx}. Stopping.")
                        found_empty = True
                        self._last_tool_index = idx
                        break
            
            if not new_tools_in_batch or found_empty:
                # No new tools received or found empty tool, assume we're done
                break
                
            current_index += batch_size
            
            # Show progress
            tools_received = len([t for t in self.tool_data.values() if t.strip()])
            print(f"    Received {tools_received} tools so far, continuing...")
        
        # Check if we received any responses at all
        if initial_packet_count == self._packets_received:
            print("[-] No tool data received - controller may not be responding")
            return
        
        self.show_tools()
        
        tools_received = len([t for t in self.tool_data.values() if t.strip()])
        if not found_empty:
            print(f"\n[+] Loaded all {tools_received} available tools.")
            self._last_tool_index = 0
        else:
            print(f"[+] Total tools loaded: {tools_received}")
        
        # Return the tool data
        return self.tool_data if self.tool_data else None
    
    def show_tools(self):
        """Display collected tool data."""
        if not self.tool_data:
            print("[-] No tool data received")
            return
            
        print("\n=== Tool List ===")
        print(f"{'Tool':<5} {'Name':<20}")
        print("-" * 30)
        
        for index in sorted(self.tool_data.keys()):
            name = self.tool_data[index]
            print(f"{index:<5} {name}")
        print("-" * 30)

    def _calculate_checksum(self, data):
        """Calculate CRC16-CCITT checksum (little-endian)."""
        crc = 0x0000
        poly = 0x1021
        
        for byte in data:
            crc ^= (byte << 8)
            for _ in range(8):
                if crc & 0x8000:
                    crc = (crc << 1) ^ poly
                else:
                    crc = crc << 1
                crc &= 0xFFFF
        
        # Return as little-endian bytes
        return crc.to_bytes(2, 'little')

    def upload_file(self, filename, max_retries=3, chunk_size=1422, remote_path=None):
        """Upload a file to the Masso controller with retry logic.
        
        Args:
            filename: Path to the file to upload
            max_retries: Maximum number of retry attempts per chunk
            chunk_size: Size of each data chunk in bytes (default: 1422)
            remote_path: Optional remote path on Masso (e.g., "projects\\part1\\file.nc")
                        If None, uses basename of filename
        Returns:
            bool: True if upload was successful, False otherwise
        """
        if not self.connected:
            print("[-] Not connected")
            return False
        
        if not self.listen_thread or not self.listen_thread.is_alive():
            print("[-] Listen thread is not running!")
            return False

        import os
        if not os.path.exists(filename):
            print(f"[-] File not found: {filename}")
            return False

        try:
            filesize = os.path.getsize(filename)
            basename = os.path.basename(filename)
            
            # Reject 0-byte files
            if filesize == 0:
                print(f"[-] Cannot upload empty file: {filename}")
                print("[-] File has 0 bytes - please ensure the file contains data")
                return False
            
            # Determine the remote filename to use
            if remote_path:
                remote_filename = remote_path
                print(f"[+] Uploading {basename} to {remote_path} ({filesize} bytes)...")
            else:
                remote_filename = basename
                print(f"[+] Uploading {basename} ({filesize} bytes)...")
            
            name_error = self._validate_remote_name(remote_filename)
            if name_error:
                print(f"[-] {name_error}")
                return False

            if chunk_size <= 0:
                print("[-] Chunk size must be positive")
                return False

            if chunk_size > 2048:
                print("[!] Warning: large chunk sizes may be rejected by the controller")

            # 1. Send Start Upload Command
            packet = self._build_start_packet(filesize, remote_filename)

            print(f"    Sending Start Upload (Length: {len(packet)} bytes)...")
            
            # Send with retry
            # Byte 5 is the accepted flag. Byte 6+ holds the previous upload's chunk
            # counter, so it must not be required to be zero.
            start_ack = self._send_with_retry(
                packet, 0x0A, "Start Upload", max_retries,
                ack_check=lambda ack: ack[5] == 0x00)
            if not start_ack:
                print("[-] Failed to start upload")
                return False

            print("    Start upload ACK received")

            # 2. Send file in chunks (match capture behavior: chunk index + length field)
            total_chunks = (filesize + chunk_size - 1) // chunk_size
            file_offset = 0
            chunk_index = 0

            with open(filename, 'rb') as f:
                while file_offset < filesize:
                    chunk = f.read(chunk_size)
                    actual_len = len(chunk)
                    if actual_len == 0:
                        break

                    # Chunk index (not byte offset) and length field, then data and trailer.
                    # A short final chunk is sent compact with its real length; the payload
                    # after the CRC must be a multiple of 4 bytes, so the trailer is 1-4 bytes.
                    # (Verified on v5.13: length 1003 needs a 2 byte trailer, 4 is ignored.)
                    is_short_final = actual_len < chunk_size
                    trailer_len = self._chunk_trailer_len(actual_len) if is_short_final else 3
                    packet = self._build_data_packet(chunk_index, chunk, trailer_len)

                    progress = min((file_offset + actual_len) / filesize * 100, 100.0)
                    print(f"\r    Sending chunk {chunk_index + 1}/{total_chunks} ({progress:.1f}%)", end='')

                    # ACK carries the next expected chunk number, little-endian from byte 6
                    def chunk_ack_ok(ack, expected=chunk_index + 1):
                        return int.from_bytes(ack[6:8], 'little') == expected

                    sent = self._send_with_retry(packet, 0x0B, f"Chunk {chunk_index + 1}",
                                                 max_retries, ack_check=chunk_ack_ok)
                    if not sent and is_short_final:
                        # Some controllers ignore the compact final packet: retry padded out
                        # to full size on the wire, keeping the real length field.
                        print("\n    Retrying final chunk as full-size packet...")
                        packet = self._build_data_packet(chunk_index, chunk, 3, pad_to=chunk_size)
                        sent = self._send_with_retry(packet, 0x0B, f"Chunk {chunk_index + 1}",
                                                     max_retries, ack_check=chunk_ack_ok)
                    if not sent:
                        print(f"\n[-] Failed to send chunk {chunk_index + 1}, aborting")
                        return False

                    chunk_index += 1
                    file_offset += actual_len

            print(f"\n[+] Upload complete - sent {chunk_index} chunks")
            return True
                
        except Exception as e:
            print(f"\n[-] Upload error: {e}")
            import traceback
            traceback.print_exc()
            return False
            
    # v5.13: 254 characters is stored intact, 255 uploads but keeps only a short 8.3 alias,
    # and 256 gets no reply (and appeared to hang the controller), so refuse it up front.
    MAX_REMOTE_NAME_LEN = 254

    @classmethod
    def apply_date_prefix(cls, remote_name, now=None):
        """Add an MMDD- prefix to the file part of a remote name.

        Returns (name, warning). The prefix is skipped when the name already starts
        with today's prefix, and when it would push the name over MAX_REMOTE_NAME_LEN
        (then the original name is returned with a warning). Any folder part and its
        separators are left as given.
        """
        prefix = (now or datetime.now()).strftime('%m%d-')
        split = max(remote_name.rfind('/'), remote_name.rfind('\\')) + 1
        folder, base = remote_name[:split], remote_name[split:]
        if base.startswith(prefix):
            return remote_name, None
        prefixed = folder + prefix + base
        if len(prefixed) > cls.MAX_REMOTE_NAME_LEN:
            return remote_name, (f"Date prefix would make filename too long "
                                 f"({len(prefixed)} chars, max {cls.MAX_REMOTE_NAME_LEN}); "
                                 f"using original name: {remote_name}")
        return prefixed, None

    @classmethod
    def _validate_remote_name(cls, name):
        """Return an error message if the remote name can't be used, else None."""
        if not name:
            return "Remote filename is empty"
        try:
            name.encode('ascii')
        except UnicodeEncodeError:
            return f"Filename must be ASCII: {name}"
        if len(name) > cls.MAX_REMOTE_NAME_LEN:
            return (f"Filename too long: {name} ({len(name)} chars, "
                    f"max {cls.MAX_REMOTE_NAME_LEN})")
        return None

    def _build_start_packet(self, filesize, remote_filename):
        """Build the start upload packet (type 0x0A).

        Format: [CRC 2][03 00][0A][size 4][00 00 01][5c 00][name ASCII][00][padding]
        The payload after the CRC is padded to a multiple of 4 bytes, with a 28 byte
        minimum (30 byte packet, as in captures). Captures show a 34 byte packet for a
        14 character name, and names of 15 to 254 characters were stored intact on v5.13
        using 16 + ceil4(len + 1) bytes.
        """
        name = remote_filename.encode('ascii')
        payload = bytearray(b'\x03\x00\x0A')
        payload.extend(filesize.to_bytes(4, 'little'))
        payload.extend(b'\x00\x00\x01')  # Unknown (3 bytes)
        payload.extend(b'\x5c\x00')      # Backslash + null
        payload.extend(name)
        payload.append(0x00)             # Null terminator
        target = max(28, 16 + ((len(name) + 1 + 3) // 4) * 4)
        payload.extend(b'\x00' * (target - len(payload)))
        return self._calculate_checksum(payload) + bytes(payload)

    @staticmethod
    def _chunk_trailer_len(chunk_len):
        """Trailer bytes for a short final chunk: payload after the CRC is a multiple of 4."""
        return (-(11 + chunk_len)) % 4 or 4

    def _build_data_packet(self, chunk_index, data, trailer_len, pad_to=None):
        """Build a data chunk (type 0x0B). The length field is always the real data length."""
        payload = bytearray(b'\x03\x00\x0B')
        payload.extend(chunk_index.to_bytes(4, 'little'))
        payload.extend(len(data).to_bytes(4, 'little'))
        payload.extend(data)
        if pad_to and len(data) < pad_to:
            payload.extend(b'\x00' * (pad_to - len(data)))
        payload.extend(b'\x00' * trailer_len)
        return self._calculate_checksum(payload) + bytes(payload)

    def _send_with_retry(self, packet, expected_ack_type, description, max_retries=3, ack_check=None):
        """Helper to send a packet and wait for ACK with retries.

        ack_check, if given, receives the 10-byte ACK and must return True to accept it.
        """
        for attempt in range(max_retries + 1):
            self._upload_ack_received.clear()
            self._last_ack_packet = None
            self.send_packet(packet, silent=True)

            # Wait for ACK with timeout
            if self._upload_ack_received.wait(timeout=2.0):
                # Check if we got the expected ACK type
                if hasattr(self, '_last_ack_type') and self._last_ack_type == expected_ack_type:
                    if ack_check is None or ack_check(self._last_ack_packet):
                        return True

            if attempt < max_retries:
                print(f"    {description} attempt {attempt + 1} failed, retrying...")
                time.sleep(0.5)

        print(f"    {description} failed after {max_retries} retries")
        return False

    def toggle_monitor(self):
        """Toggle status monitor mode."""
        self.monitor_mode = not self.monitor_mode
        state = "enabled" if self.monitor_mode else "disabled"
        print(f"[*] Monitor mode {state}")
        if self.monitor_mode:
            self.show_status()
    
    def monitor_mode_with_duration(self, duration):
        """Run monitor mode for specified duration."""
        print(f"Starting status monitor for {duration} seconds...")
        self.toggle_monitor()
        
        try:
            # Run for specified duration
            start_time = time.time()
            while time.time() - start_time < duration:
                time.sleep(0.1)
            print(f"\n[*] Monitor duration of {duration} seconds completed")
        finally:
            # Always disable monitor mode when done
            if self.monitor_mode:
                self.toggle_monitor()

    def toggle_logging(self):
        """Toggle logging to file."""
        if self.log_file:
            self.log_file.close()
            self.log_file = None
            print("[+] Logging stopped")
        else:
            filename = f"masso_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            try:
                self.log_file = open(filename, 'w')
                print(f"[+] Logging started to {filename}")
            except Exception as e:
                print(f"[-] Error opening log file: {e}")

    def _load_watch_state(self, directory):
        """Load watch state from JSON file."""
        state_file = os.path.join(directory, '.masso_watch_state.json')
        try:
            if os.path.exists(state_file):
                with open(state_file, 'r') as f:
                    return json.load(f)
        except Exception as e:
            print(f"[*] Info: Could not load watch state (will start fresh): {e}")
        return {}
    
    def _save_watch_state(self, directory, state):
        """Save watch state to JSON file."""
        state_file = os.path.join(directory, '.masso_watch_state.json')
        try:
            with open(state_file, 'w') as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            print(f"[!] Warning: Could not save watch state: {e}")
    
    def _get_file_key(self, filepath):
        """Generate a unique key for a file based on path, size, and mtime."""
        try:
            stat = os.stat(filepath)
            return {
                'path': filepath,
                'size': stat.st_size,
                'mtime': stat.st_mtime
            }
        except:
            return None

    def _log(self, message):
        """Log message to file if enabled."""
        if self.log_file:
            timestamp = datetime.now().strftime('%H:%M:%S.%f')[:-3]
            self.log_file.write(f"[{timestamp}] {message}\n")
            self.log_file.flush()

    def _keepalive_loop(self):
        """Send keepalives periodically."""
        print("[*] Keepalive thread started")
        
        # Build keepalive packet once (it doesn't change)
        keepalive_packet = self._build_keepalive_packet()
        
        while self.connected:
            try:
                # Send Keepalive
                self.send_packet(keepalive_packet, silent=True)
                time.sleep(1.0)
            except Exception as e:
                print(f"[-] Keepalive error: {e}")
                break
    
    def _listen_loop(self):
        """Listen for incoming packets."""
        while self.listening:
            try:
                data, addr = self.socket.recvfrom(4096)
                self._packets_received += 1  # Increment packet counter
                
                # Debug logging for all packets (filter out noise)
                if self.debug:
                    # Skip keepalive responses (10-byte packets that aren't config or upload ACKs)
                    is_keepalive = (len(data) == 10 and data[4] not in (0x03, 0x0A, 0x0B))
                    
                    # Skip duplicate status packets
                    is_duplicate_status = (len(data) == 270 and self.last_status and data == self.last_status)
                    
                    if not is_keepalive and not is_duplicate_status:
                        print(f"[DEBUG] Packet {self._packets_received}: {len(data)} bytes from {addr}")
                        print(f"[DEBUG] Raw data: {data.hex()}")
                
                # Handle Version Response
                if len(data) == 46:
                    version = data[12:].decode('ascii', errors='ignore').strip('\x00')
                    print(f"\n[<] Received Version: {version}")
                    self._log(f"Received Version: {version}")
                
                # Handle Upload ACKs (Type 0x0A, 0x0B)
                # IMPORTANT: Upload ACKs must be checked BEFORE general 10-byte packets
                # because upload ACKs are also 10 bytes and would otherwise be caught
                # by the configuration response handler (Type 0x03)
                elif len(data) == 10:
                    pkt_type = data[4]
                    
                    # Check for upload ACK packets
                    if pkt_type in (0x0A, 0x0B):
                        # Store the ACK packet type for _send_with_retry
                        self._last_ack_type = pkt_type
                        self._last_ack_packet = data
                        
                        # Signal that we received an ACK
                        if hasattr(self, '_upload_ack_received'):
                            self._upload_ack_received.set()
                        
                        if self.monitor_mode:
                            ack_type = "Start Upload" if pkt_type == 0x0A else "Data"
                            print(f"\n[<] Received {ack_type} ACK ({len(data)} bytes)")
                    
                    # Handle Configuration Response (Type 0x03)
                    elif pkt_type == 0x03:
                        # Extract serial number from bytes 5-6 (little-endian)
                        serial = int.from_bytes(data[5:7], 'little')
                        print(f"[<] Configuration response received")
                        print(f"    Controller Serial: {serial}")
                        self._log(f"Controller Serial: {serial}")
                    
                    # Handle Config Ack (other small packets)
                    else:
                        pass
                
                # Handle Status Data
                elif len(data) == 270:
                    now = time.monotonic()
                    if self.monitor_mode and self.last_status:
                        # Compare with previous status
                        diffs = []
                        decoded_msgs = []
                        
                        # Check for meaningful changes first
                        progress_changed = data[5] != self.last_status[5]
                        run_active_changed = data[6] != self.last_status[6]
                        line_changed = data[13] != self.last_status[13]
                        # Check full filename range (17-80) for changes
                        filename_changed = data[17:80] != self.last_status[17:80]
                        
                        # Decode Progress
                        if progress_changed:
                            decoded_msgs.append(f"Progress: {data[5]}%")

                        # Decode Execution State
                        if run_active_changed:
                            run_state = "Running" if data[6] == 0x02 else "Not Running"
                            decoded_msgs.append(f"Execution: {run_state} (0x{data[6]:02x})")
                        
                        # Decode Filename
                        if filename_changed:
                            filename_bytes = data[17:80]
                            if b'\x00' in filename_bytes:
                                filename = filename_bytes.split(b'\x00', 1)[0].decode('ascii', errors='ignore')
                            else:
                                filename = filename_bytes.decode('ascii', errors='ignore').strip()
                            
                            if filename:
                                decoded_msgs.append(f"File: {filename}")
                            else:
                                decoded_msgs.append("File: (cleared)")
                        
                        # Decode Line Number
                        if line_changed:
                            decoded_msgs.append(f"Line: {data[13]}")

                        # Decode Byte 12 (User Prompt / Tool Change State)
                        if data[12] != self.last_status[12]:
                            if data[12] == 0x00:
                                decoded_msgs.append("Waiting for tool change")
                            elif data[12] == 0x01:
                                decoded_msgs.append("User Input Completed/Resumed")
                            else:
                                decoded_msgs.append(f"User Prompt State: 0x{data[12]:02x}")

                        # Investigate Bytes 14-16 (always 0x00 in captures - log if they ever change)
                        if data[14:17] != self.last_status[14:17]:
                            b14 = f"0x{data[14]:02x}"
                            b15 = f"0x{data[15]:02x}"
                            b16 = f"0x{data[16]:02x}"
                            decoded_msgs.append(f"[Inv] Bytes 14-16 changed: [{b14},{b15},{b16}] (expected 0x00)")

                        # Check for other (unknown) changes
                        # Ignored: 0-1 (checksum), 5 (progress), 6 (exec state), 7 (0xFF constant),
                        #          8-11 (job count), 12 (user prompt), 13 (line), 14-16 (const 0x00), 17-80 (filename)
                        ignored_indices = {0, 1, 5, 6, 7, 12, 13, 14, 15, 16} | set(range(8, 12)) | set(range(17, 81))
                        
                        for i in range(len(data)):
                            if data[i] != self.last_status[i]:
                                if i not in ignored_indices:
                                    diffs.append(f"[{i}]: {self.last_status[i]:02x}->{data[i]:02x}")
                        
                        # Print Output
                        if decoded_msgs:
                            msg = " | ".join(decoded_msgs)
                            print(f"\n[Status] {msg}")
                            self._log(f"Status: {msg}")
                            
                        if diffs:
                            msg = f"Unknown Changes: {' '.join(diffs)}"
                            print(f"  {msg}")
                            self._log(msg)
                            
                    self.last_status = data

                    # Extract job count (bytes 8-11, little-endian)
                    job_count = int.from_bytes(data[8:12], byteorder='little')
                    if self.job_count != job_count:
                        self.job_count = job_count
                        print(f"\n[Status] Job Count: {job_count}")
                        self._log(f"Job count: {job_count}")

                    # Track feed hold state (line stalled while running)
                    line_value = data[13]
                    line_changed = self._last_line_value is None or line_value != self._last_line_value
                    state_running = data[6] == 0x02

                    if not state_running:
                        if self._in_feed_hold and self.monitor_mode:
                            print("[Status] Feed Hold released")
                            self._log("Feed Hold released")
                        self._in_feed_hold = False
                        self._last_line_change_time = now
                        self._last_line_value = line_value
                    elif line_changed:
                        self._last_line_change_time = now
                        self._last_line_value = line_value
                        if self._in_feed_hold and self.monitor_mode:
                            print("[Status] Feed Hold released")
                            self._log("Feed Hold released")
                        self._in_feed_hold = False
                    else:
                        if (self._last_line_change_time is not None
                                and not self._in_feed_hold
                                and line_value > 0
                                and now - self._last_line_change_time >= self._feed_hold_threshold):
                            self._in_feed_hold = True
                            print("[Status] Feed Hold active (line stalled)")
                            self._log("Feed Hold active")

                # Handle Tool Data (38 bytes, Type 0x08)
                elif len(data) == 38 and data[4] == 0x08:
                    tool_index = data[5]
                    # Name starts at byte 6. It is null-terminated.
                    # We must split at the first null byte to avoid reading garbage.
                    raw_name = data[6:]
                    if b'\x00' in raw_name:
                        name_bytes = raw_name.split(b'\x00', 1)[0]
                    else:
                        name_bytes = raw_name
                    
                    name = name_bytes.decode('ascii', errors='ignore')
                    self.tool_data[tool_index] = name
                    self._log(f"Tool {tool_index}: {name}")
                
                else:
                    if self.debug and len(data) == 38:
                        print(f"[DEBUG] 38-byte packet not matching tool condition: data[4]={data[4]:02x}")
                    
            except Exception as e:
                if self.listening:
                    print(f"[-] Receive error: {e}")
    
    def send_packet(self, data, silent=False, message=None):
        """Send a packet."""
        if self.socket:
            self.socket.sendto(data, (self.host, self.controller_port))
            if not silent:
                if message:
                    print(f"[>] {message}")
                else:
                    print(f"[>] Sent {len(data)} bytes")
    
    def test_packet(self, hex_data, timeout=2.0, capture_response=True):
        """Send a custom hex packet and optionally capture response.
        
        Args:
            hex_data: Hex string to send (e.g., "03 00 02 f8 2a 00 00 0b")
            timeout: Seconds to wait for response
            capture_response: Whether to capture and return response
            
        Returns:
            dict: {'sent': bytes, 'response': bytes or None, 'response_time': float}
        """
        if not self.socket:
            print("[-] No socket available - start client first")
            return None
        
        try:
            # Parse hex data
            clean_hex = hex_data.replace(' ', '').replace('\n', '')
            packet = bytes.fromhex(clean_hex)
            
            # Record packet count before sending
            initial_count = self._packets_received
            start_time = time.time()
            
            # Send packet
            print(f"[>] Testing packet: {hex_data}")
            print(f"[>] Sending {len(packet)} bytes: {packet.hex()}")
            self.send_packet(packet, silent=True)
            
            # Capture response if requested
            response = None
            response_time = None
            
            if capture_response:
                # Wait for response
                time.sleep(timeout)
                final_count = self._packets_received
                
                if final_count > initial_count:
                    response_time = time.time() - start_time
                    print(f"[<] Response received in {response_time:.3f}s")
                    
                    # Try to get the last received packet
                    # Note: This is a simple approach - for more precise capture,
                    # we'd need to modify the _listen_loop to store packets
                    print(f"[<] Packets received: {final_count - initial_count}")
                else:
                    print(f"[-] No response received in {timeout}s")
            
            return {
                'sent': packet,
                'response': response,
                'response_time': response_time,
                'packets_received': self._packets_received - initial_count
            }
            
        except ValueError as e:
            print(f"[-] Invalid hex data: {e}")
            return None
        except Exception as e:
            print(f"[-] Error sending packet: {e}")
            return None
    
    def test_packet_sequence(self, packets, delay=0.5):
        """Send a sequence of packets for protocol testing.
        
        Args:
            packets: List of hex strings to send in sequence
            delay: Delay between packets in seconds
            
        Returns:
            List of response dictionaries
        """
        results = []
        
        print(f"[*] Testing packet sequence ({len(packets)} packets)")
        print("=" * 60)
        
        for i, hex_data in enumerate(packets, 1):
            print(f"\n--- Packet {i}/{len(packets)} ---")
            result = self.test_packet(hex_data, timeout=delay)
            if result:
                results.append(result)
            
            if i < len(packets):
                time.sleep(delay)
        
        print("\n" + "=" * 60)
        print(f"[*] Sequence complete: {len(results)} packets sent")
        
        return results

    # --- Directory watch helpers ---

    def start_auto_upload(self, directory, poll_interval=2.0, date_prefix=False):
        """Watch a directory for .nc files and upload new/updated ones."""
        if not os.path.isdir(directory):
            print(f"[-] Directory not found: {directory}")
            return False

        directory = os.path.abspath(directory)

        if self._watch_thread and self._watch_thread.is_alive():
            print("[!] Auto-upload already running. Stop it first with 'watchoff'.")
            return False

        if not self.connected:
            print("[-] Cannot start auto-upload: not connected to controller")
            return False

        self._watch_directory = directory
        self._watch_date_prefix = date_prefix  # Store date prefix setting
        self._watch_stop_event.clear()
        self._watch_thread = threading.Thread(
            target=self._watch_directory_loop,
            args=(poll_interval,),
            daemon=True,
        )
        self._watch_thread.start()
        if date_prefix:
            print(f"[+] Auto-upload watching {directory} with date prefix (poll every {poll_interval}s)")
        else:
            print(f"[+] Auto-upload watching {directory} (poll every {poll_interval}s)")
        return True

    def stop_auto_upload(self):
        """Stop the directory watcher if running."""
        if self._watch_thread and self._watch_thread.is_alive():
            print("[+] Stopping auto-upload watcher...")
            self._watch_stop_event.set()
            self._watch_thread.join(timeout=5.0)
        self._watch_thread = None
        self._watch_directory = None
        self._watch_date_prefix = None  # Clean up date prefix setting
        self._watch_stop_event.clear()
    
    def clear_watch_state(self, directory):
        """Clear the watch state file for a directory."""
        state_file = os.path.join(directory, '.masso_watch_state.json')
        try:
            if os.path.exists(state_file):
                os.remove(state_file)
                print(f"[+] Cleared watch state for {directory}")
            else:
                print(f"[!] No watch state file found for {directory}")
        except Exception as e:
            print(f"[-] Error clearing watch state: {e}")

    def _watch_directory_loop(self, poll_interval):
        print("[*] Auto-upload watcher started")
        
        # Load previous state
        uploaded_files = self._load_watch_state(self._watch_directory)
        
        while not self._watch_stop_event.is_set():
            try:
                for entry in os.scandir(self._watch_directory):
                    if not entry.is_file():
                        continue
                    if not entry.name.lower().endswith('.nc'):
                        continue

                    path = entry.path
                    file_key = self._get_file_key(path)
                    
                    if not file_key:
                        continue
                    
                    # Check if file was already uploaded with same size and mtime
                    file_id = f"{file_key['path']}:{file_key['size']}:{file_key['mtime']}"
                    if file_id in uploaded_files:
                        continue
                    
                    print(f"[+] Detected new/changed NC file: {os.path.basename(path)}")

                    if not self.connected:
                        print("[-] Skipping upload (not connected)")
                        continue
                    
                    # Check filename length before attempting upload
                    basename = os.path.basename(path)
                    
                    # Apply date prefix if enabled
                    remote_filename = basename
                    if hasattr(self, '_watch_date_prefix') and self._watch_date_prefix:
                        remote_filename, warning = self.apply_date_prefix(basename)
                        if warning:
                            print(f"[-] {warning}")
                    
                    if len(remote_filename) > self.MAX_REMOTE_NAME_LEN:
                        print(f"[-] Skipping {remote_filename} - filename too long ({len(remote_filename)} chars, max {self.MAX_REMOTE_NAME_LEN})")
                        # Add to state to prevent repeated checks
                        uploaded_files[file_id] = {
                            'uploaded_at': time.time(),
                            'path': path,
                            'size': file_key['size'],
                            'mtime': file_key['mtime'],
                            'status': 'skipped_invalid_filename'
                        }
                        self._save_watch_state(self._watch_directory, uploaded_files)
                        continue

                    success = self.upload_file(path, remote_path=remote_filename)
                    if success:
                        print(f"[+] Auto-upload succeeded for {path}")
                        # Record successful upload
                        uploaded_files[file_id] = {
                            'uploaded_at': time.time(),
                            'path': path,
                            'size': file_key['size'],
                            'mtime': file_key['mtime'],
                            'status': 'uploaded'
                        }
                        self._save_watch_state(self._watch_directory, uploaded_files)
                    else:
                        print(f"[-] Auto-upload FAILED for {path}")
                        # Add to state to prevent immediate retry (will retry if file changes)
                        uploaded_files[file_id] = {
                            'uploaded_at': time.time(),
                            'path': path,
                            'size': file_key['size'],
                            'mtime': file_key['mtime'],
                            'status': 'failed'
                        }
                        self._save_watch_state(self._watch_directory, uploaded_files)

            except Exception as exc:
                print(f"[!] Auto-upload error: {exc}")

            self._watch_stop_event.wait(poll_interval)

        print("[*] Auto-upload watcher stopped")
    
    def show_status(self):
        """Show decoded status."""
        if not self.last_status:
            print("[-] No status data received yet")
            return
        
        data = self.last_status
        
        # Decode known fields
        progress_val = data[5]
        run_active_flag = data[6]
        job_count = int.from_bytes(data[8:12], byteorder='little')
        line_number = data[13]
        
        # Decode filename (starts at byte 17, null-terminated)
        filename_bytes = data[17:80]  # Read up to 63 bytes for filename
        if b'\x00' in filename_bytes:
            filename = filename_bytes.split(b'\x00', 1)[0].decode('ascii', errors='ignore')
        else:
            filename = filename_bytes.decode('ascii', errors='ignore').strip()
        
        # Decode state
        run_state = "Running" if run_active_flag == 0x02 else "Not Running"
        
        print("\n=== Machine Status ===")
        print(f"Progress: {progress_val}%")
        print(f"Execution: {run_state} (0x{run_active_flag:02x})")
        print(f"Job Count: {job_count}")
        print(f"Current File: {filename if filename else '(none)'}")
        print(f"Line Number: {line_number}")
        if self._in_feed_hold:
            print("Feed Hold: ACTIVE (line stalled)")
        print(f"\nRaw packet length: {len(data)} bytes")
            
    def close(self):
        """Stop client."""
        self.stop_auto_upload()
        self.connected = False
        self.listening = False
        if self.socket:
            self.socket.close()
        if self.log_file:
            self.log_file.close()


def tools_mode(client, enable_logging=False):
    """Run tools mode - get tool list and exit."""
    if enable_logging:
        client.toggle_logging()
    
    print("Getting tool list...")
    client.get_tools(client._last_tool_index)
    print("Tool list complete.")

def upload_mode(client, args, enable_logging=False):
    """Run upload mode - upload file(s) and exit."""
    if enable_logging:
        client.toggle_logging()
    
    for upload_spec in args.upload:
        parts = upload_spec.split(':', 1)
        if len(parts) == 2:
            local_file, remote_path = parts
        else:
            local_file, remote_path = upload_spec, None

        if args.date_prefix:
            # Prefix the remote name (or the local file name when none is given)
            remote_path, warning = client.apply_date_prefix(remote_path or os.path.basename(local_file))
            if warning:
                print(f"[-] {warning}")
        client.upload_file(local_file, remote_path=remote_path)

def watch_mode(client, args, enable_logging=False):
    """Run watch mode - monitor directory for changes."""
    if enable_logging:
        client.toggle_logging()
    
    directory = args.watch if args.watch else '.'
    print(f"Starting auto-upload watcher for {directory}...")
    client.start_auto_upload(directory, date_prefix=args.date_prefix)
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping watcher...")
        client.stop_auto_upload()

def monitor_mode(client, enable_logging=False, duration=None):
    """Run monitor mode - show real-time status."""
    if enable_logging:
        client.toggle_logging()
    
    print("Starting status monitor...")
    if duration:
        print(f"[*] Will run for {duration} seconds")
    client.toggle_monitor()
    
    try:
        if duration:
            # Run for specified duration
            start_time = time.time()
            while time.time() - start_time < duration:
                time.sleep(0.1)
            print(f"\n[*] Monitor duration of {duration} seconds completed")
        else:
            # Run forever until interrupted
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping monitor...")
    finally:
        client.toggle_monitor()

def interactive_mode(host, debug=False):
    """Run interactive CLI."""
    if not host:
        print("[-] Error: Host IP address is required. Use --host <IP> argument.")
        return
    
    client = MassoClient(host=host, debug=debug)
    if not client.start():
        return
    
    print("\nMASSO Client Ready")
    print("Auto-connecting...")
    if not client.connect():
        print(f"\n[-] Failed to connect to {host}")
        print("[*] Please check:")
        print("    - The IP address is correct")
        print("    - The MASSO controller is powered on")
        print("    - Network connectivity to the controller")
        return
    
    # Try to get status to verify the connection is working
    print("[*] Verifying connection...")
    time.sleep(2)  # Give more time for responses
    
    # Check if we received any packets
    if client._packets_received > 0:
        print(f"[+] Connection verified - received {client._packets_received} packet(s)")
    else:
        print(f"\n[-] No response from {host}")
        print("[*] This could mean:")
        print("    - The controller is not responding")
        print("    - Firewall is blocking responses (port 65535)")
        print("    - Wrong IP address or controller is offline")
        print("[*] Try the 'status' command to check manually")
    
    print("\nCommands: status, tools, monitor, log, upload <file>, watch <dir>, watchoff, clearwatch <dir>, send <hex>, connect [IP], handshake, subscribe, quit")
    
    try:
        while True:
            cmd = input("\nmasso> ").strip() # Don't lower() to preserve filename case
            cmd_lower = cmd.lower()
            
            if cmd_lower == 'quit':
                break
            elif cmd_lower == 'connect':
                client.connect()
            elif cmd_lower.startswith('connect '):
                # Allow changing IP address
                new_host = cmd[8:].strip()
                if new_host:
                    old_host = client.host
                    client.host = new_host
                    print(f"[*] Changed host from {old_host} to {new_host}")
                    client.connect()
                else:
                    print("Usage: connect <IP address>")
            elif cmd_lower == 'status':
                client.show_status()
            elif cmd_lower == 'tools':
                # Load tools progressively until empty tool found
                client.get_tools(client._last_tool_index)
            elif cmd_lower == 'monitor':
                client.toggle_monitor()
            elif cmd_lower == 'log':
                client.toggle_logging()
            elif cmd_lower.startswith('upload '):
                parts = cmd[7:].strip().split(maxsplit=1)
                if len(parts) == 1:
                    # Just filename, no remote path
                    client.upload_file(parts[0])
                elif len(parts) == 2:
                    # Filename and remote path
                    client.upload_file(parts[0], remote_path=parts[1])
                else:
                    print("Usage: upload <local_file> [remote_path]")
            elif cmd_lower.startswith('watch '):
                directory = cmd[6:].strip() or '.'
                client.start_auto_upload(directory)
            elif cmd_lower == 'watchoff':
                client.stop_auto_upload()
            elif cmd_lower.startswith('clearwatch '):
                directory = cmd[11:].strip() or '.'
                client.clear_watch_state(directory)
            elif cmd_lower.startswith('send '):
                try:
                    hex_str = cmd[5:].replace(' ', '')
                    data = bytes.fromhex(hex_str)
                    client.send_packet(data)
                except:
                    print("Invalid hex")
            elif cmd:
                print("Unknown command")
                
    except KeyboardInterrupt:
        print("\nExiting...")
    finally:
        client.close()

def main():
    """Main entry point with argument parsing."""
    import argparse
    
    parser = argparse.ArgumentParser(description='MASSO UDP Client')
    parser.add_argument('--host', required=True, metavar='IP',
                       help='MASSO controller IP address')
    parser.add_argument('--interactive', action='store_true', 
                       help='Run in interactive mode')
    parser.add_argument('--tools', action='store_true',
                       help='Get tool list and exit')
    parser.add_argument('--status', action='store_true',
                       help='Show current status and exit')
    parser.add_argument('--upload', nargs='+', metavar='FILE[:REMOTE]',
                       help='Upload file(s). Use FILE:REMOTE to specify remote path')
    parser.add_argument('--date-prefix', action='store_true',
                       help='Add short date prefix (MMDD-) to uploaded filenames for easier identification')
    parser.add_argument('--watch', nargs='?', const='.', metavar='DIR',
                       help='Watch directory for .nc file changes (default: current directory)')
    parser.add_argument('--monitor', action='store_true',
                       help='Monitor real-time status')
    parser.add_argument('--duration', type=int, metavar='SECONDS',
                       help='Run monitor mode for specified seconds, then exit (requires --monitor)')
    parser.add_argument('--log', action='store_true',
                       help='Enable logging to file')
    parser.add_argument('--test-packet', metavar='HEX',
                       help='Send custom hex packet and capture response')
    parser.add_argument('--test-sequence', metavar='FILE',
                       help='Send packet sequence from file (one hex packet per line)')
    parser.add_argument('--debug', action='store_true',
                       help='Enable debug mode - log all raw packets')
    
    args = parser.parse_args()
    
    # Default to interactive mode if no other mode specified
    if not any([args.tools, args.upload, args.watch is not None, args.monitor, args.test_packet, args.test_sequence]):
        args.interactive = True
    
    try:
        if args.interactive:
            interactive_mode(args.host, args.debug)
        else:
            # Create and start client for non-interactive modes
            client = MassoClient(host=args.host, debug=args.debug)
            if not client.start():
                print("Failed to start client")
                return 1
            
            # Connect for non-interactive modes
            print("Connecting to MASSO...")
            client.connect()
            
            if args.tools:
                tools_mode(client, args.log)
            elif args.upload:
                upload_mode(client, args, args.log)
            elif args.watch is not None:
                watch_mode(client, args, args.log)
            elif args.monitor:
                monitor_mode(client, args.log, args.duration)
            elif args.test_packet:
                print(f"[*] Testing packet: {args.test_packet}")
                result = client.test_packet(args.test_packet, timeout=3.0)
                if result:
                    print(f"[*] Test complete: {result['packets_received']} responses received")
            elif args.test_sequence:
                try:
                    with open(args.test_sequence, 'r') as f:
                        packets = [line.strip() for line in f if line.strip() and not line.startswith('#')]
                    
                    results = client.test_packet_sequence(packets, delay=1.0)
                    
                    print(f"\n[*] Sequence results:")
                    for i, result in enumerate(results, 1):
                        print(f"  Packet {i}: {result['packets_received']} responses")
                    
                except FileNotFoundError:
                    print(f"[-] File not found: {args.test_sequence}")
                    return 1
    finally:
        # Only close client if it was created for non-interactive modes
        if not args.interactive:
            client.close()
    
    return 0

if __name__ == '__main__':
    import sys
    sys.exit(main())
