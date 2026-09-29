# Masso Link Command Line Client

**⚠️ EXPERIMENTAL SOFTWARE - WORK IN PROGRESS**

**Disclaimer**: This is an unofficial, experimental implementation of the MASSO Link protocol. This software is:
- **NOT** supported by or affiliated with MASSO
- **NOT** guaranteed to work with all MASSO controllers or firmware versions (currently tested with v5.09)
- **ONLY** tested with controller in Lathe mode - may not work or need changes for Mill/Router modes - Please test on your non-Lathe controller!
- **ONLY** tested on my own personal device so far - community testing needed
- **NOT** suitable for production use or critical applications
- Provided "AS IS" without any warranties or guarantees

**Use at your own risk**. The authors assume no liability for any damage to equipment, loss of data, or other consequences arising from the use of this software.

**Testing & Feedback**: This implementation is experimental and benefits from community testing. Please test with your MASSO controller configuration and report results, issues, or protocol discoveries via GitHub issues or pull requests.

---

## Motivation

This project was created to address limitations in the official MASSO Link software and to enable protocol exploration:

### Key Goals
- **Watch Directory Functionality**: Automatically upload NC files when they're created or modified, enabling seamless development workflows
- **Multi-File Transfer**: Upload multiple files in sequence without manual intervention
- **Scriptable Operations**: Enable command line file transfers and integration with other tools.
- **Protocol Learning**: Further understand the MASSO Link protocol for community understanding

### Use Cases
- **Development**: Watch a build directory and automatically upload compiled G-code
- **Batch Operations**: Upload entire project directories with a single command
- **Automation**: Integrate with automated workflows
- **Protocol Research**: Discover and document undocumented protocol features

### Future Ideas
- **Locally Hosted Web Interface**: Browser-based control panel for monitoring and file management
- **Cross-Platform GUI App**: Desktop application with visual interface for all operations
- **CAM Software Integration**: Post-processor step for direct upload from CAM tools
- **Real-Time Status Dashboard**: Web-based monitoring of machine status and operations
- and more

---

A Python-based command-line tool for communicating with the Masso Link controller protocol.

**⚠️ Important**: Do not run multiple instances of this client or use the official MASSO Link app simultaneously. The controller broadcasts responses on UDP port 65535, which all listening clients receive. Running multiple clients can cause ACK confusion, upload failures, and unpredictable behavior as clients may intercept each other's responses.

## Getting Started

**Prerequisites**: Python 3.6+ (no dependencies required)

1. **Clone or download this repository**
   ```bash
   git clone https://github.com/andrewpc/masso-link-protocol-client.git
   cd masso_client
   ```

2. **Find your MASSO controller's IP address** (check controller network settings or router DHCP list)

3. **Upload a file** (CLI mode - recommended)
   ```bash
   python masso_udp_client.py --host 192.168.1.32 --upload myfile.nc
   ```

3. **Watch a directory for auto-upload**
   ```bash
   python masso_udp_client.py --host 192.168.1.32 --watch /path/to/nc/files
   ```

4. **Monitor machine status**
   ```bash
   python masso_udp_client.py --host 192.168.1.32 --monitor
   ```

**Note**: Filenames can be up to 254 characters (ASCII only). For interactive mode, run without flags: `python masso_udp_client.py --host <IP>`

## Usage

### Interactive Mode (default)
```bash
python masso_udp_client.py --host <IP>
# Note: --host is always required
```

### Non-Interactive Modes

**Specify Controller IP**
```bash
# All commands require the controller IP
python masso_udp_client.py --host 192.168.1.50 --tools
python masso_udp_client.py --host 10.0.0.100 --upload test.nc
python masso_udp_client.py --host 192.168.1.50 --watch
```

**Get Tool List**
```bash
python masso_udp_client.py --host <IP> --tools
python masso_udp_client.py --host <IP> --tools --log  # With logging
```

**Upload Files**
```bash
# Upload single file
python masso_udp_client.py --host <IP> --upload test.nc

# Upload multiple files
python masso_udp_client.py --host <IP> --upload test.nc file2.nc

# Upload with custom remote filename
python masso_udp_client.py --host <IP> --upload test.nc:my_program.nc

# Upload to existing remote directory
python masso_udp_client.py --host <IP> --upload test.nc:subdir\my_program.nc

# Upload with date prefix (MMDD- format for easy identification)
python masso_udp_client.py --host <IP> --upload test.nc --date-prefix
# Uploads as: "1130-test.nc" (Nov 30th)

# Upload multiple files with date prefix
python masso_udp_client.py --host <IP> --upload file1.nc file2.nc --date-prefix
# Uploads as: "1130-file1.nc", "1130-file2.nc"

# Upload with logging
python masso_udp_client.py --host <IP> --upload test.nc --log

# Upload multiple files using shell glob patterns
python masso_udp_client.py --host <IP> --upload *.nc --date-prefix
# Note: *.nc expansion depends on shell and existing .nc files in current directory
# Works when: .nc files exist and shell expands the pattern
# Fails when: No .nc files exist (shell passes "*.nc" as literal filename)
```

**Watch Directory**
```bash
# Watch current directory
python masso_udp_client.py --host <IP> --watch

# Watch specific directory
python masso_udp_client.py --host <IP> --watch /path/to/nc/files

# Watch with date prefix for automatic file identification
python masso_udp_client.py --host <IP> --watch --date-prefix
# Auto-uploads new files as: "1130-filename.nc"

# Watch specific directory with date prefix and logging
python masso_udp_client.py --host <IP> --watch /path/to/nc/files --date-prefix --log

# Watch with logging
python masso_udp_client.py --host <IP> --watch --log
```

**Monitor Status**
```bash
python masso_udp_client.py --host <IP> --monitor
python masso_udp_client.py --host <IP> --monitor --log  # With logging
python masso_udp_client.py --host <IP> --monitor --duration 30  # Run for 30 seconds
python masso_udp_client.py --host <IP> --monitor --duration 10 --debug  # Debug for 10 seconds
```

**Packet Testing**
```bash
# Send custom hex packet
python masso_udp_client.py --host <IP> --test-packet "03 00 02 f8 2a 00 00 0b"

# Send packet with debug output
python masso_udp_client.py --host <IP> --debug --test-packet "03 00 04 01"

# Test packet sequence from file (one hex packet per line)
python masso_udp_client.py --host <IP> --test-sequence packets.txt

# Examples for protocol exploration
python masso_udp_client.py --host <IP> --test-packet "03 00 08 01"  # Tool request
python masso_udp_client.py --host <IP> --test-packet "03 00 06 00"  # Job control
```

**Debug Mode**
```bash
# Debug mode works with all commands to show raw packet data
python masso_udp_client.py --host <IP> --tools --debug
python masso_udp_client.py --host <IP> --upload test.nc --debug
python masso_udp_client.py --host <IP> --monitor --debug
python masso_udp_client.py --host <IP> --watch --debug
python masso_udp_client.py --host <IP> --test-packet "03 00 02 f8 2a 00 00 0b" --debug
```

**Logging**
The `--log` flag works with all modes to save activity to a timestamped log file.

**Important**: The `--host` argument is required for all commands.

## Features

- **UDP Communication**: Send and receive UDP packets to/from MASSO
- **Background Listener**: Automatically listens for broadcasts on port 65535
- **Interactive Mode**: Send commands and observe responses in real-time
- **Status Monitoring**: Real-time machine status display with feed hold detection
- **Tool Management**: Request and display tool list from the controller
- **File Upload**: Upload G-code/NC files via UDP protocol
- **Date Prefix Uploads**: Optional MMDD- prefix for easy file identification
- **Auto-upload Watcher**: Monitor directory for automatic file uploads
- **Packet Testing**: Send custom hex packets for protocol exploration
- **Packet Sequences**: Test multiple packets in sequence from file
- **Packet Logging**: Log all activity to timestamped files
- **Dynamic Packet Generation**: No hardcoded packets - all generated programmatically

## Installation

No external dependencies required - uses Python standard library only.

Requires Python 3.6 or higher.

## Commands

### Interactive Mode Commands
Once in interactive mode, available commands:
- `status` - Show current machine status
- `tools` - Request and display the tool list
- `monitor` - Toggle real-time status monitoring (shows changes as they happen)
- `log` - Toggle logging to file (creates timestamped log files)
- `upload <file>` - Upload a G-code/NC file to the controller
- `upload <file> <remote_path>` - Upload to specific remote path
- `watch <directory>` - Start auto-upload watcher for .nc files
- `watchoff` - Stop the auto-upload watcher
- `clearwatch <directory>` - Clear watch state for a directory
- `send <hex>` - Send raw hex packet (for testing)
- `connect` - Reconnect to the controller
- `connect <IP>` - Connect to specific IP address
- `quit` - Exit the client

### Command-Line Options
- `--host IP` - MASSO controller IP address (required)
- `--interactive` - Run in interactive mode (default)
- `--tools` - Get tool list and exit
- `--upload FILE[:REMOTE]` - Upload file(s). Use FILE:REMOTE to specify remote path
- `--watch [DIR]` - Watch directory for .nc file changes (default: current directory)
- `--monitor` - Monitor real-time status
- `--duration SECONDS` - Run monitor mode for specified seconds, then exit (requires --monitor)
- `--log` - Enable logging to file (works with all modes)
- `--debug` - Enable debug mode to log all raw packets (filters out keepalive responses and duplicate status packets)

### Upload Files (Interactive Mode)
```bash
# Upload a single file
upload test_upload_file/adaptive.nc

# Upload with custom remote filename
upload test_upload_file/adaptive.nc my_program.nc

# Auto-upload directory (watches for .nc file changes)
watch .                    # Watch current directory
watch test_upload_file/     # Watch specific directory
watchoff                    # Stop watching
```

**Important Notes**:
- See "Filename Requirements" section for upload constraints
- Subdirectories: Only if they already exist on the MASSO
- Use backslash `\` for subdirectories, not forward slash `/`

## Protocol Implementation

The client implements the MASSO UDP protocol. For detailed protocol specifications including packet structures, types, and field definitions, see [PROTOCOL_SPEC.md](PROTOCOL_SPEC.md).

## File Upload

Supports uploading G-code/NC files to the controller:
- Automatic file chunking with retry logic
- Progress reporting
- Remote path specification
- Auto-upload watcher for continuous development

### Watch Mode State Tracking

The watch mode prevents duplicate uploads by maintaining state:
- **State File**: `.masso_watch_state.json` in the watched directory
- **Tracks**: File path, size, modification time, upload timestamp, and status
- **Behavior**: 
  - Files are only uploaded if size or modification time changes
  - State persists between client restarts
  - Missing or corrupted state files are handled gracefully (starts fresh)
  - Files with invalid names (>254 chars) are skipped and remembered
  - Failed uploads are tracked to prevent immediate retry loops
  - Use `clearwatch <dir>` command to reset the state file
  - Or manually delete `.masso_watch_state.json` from the directory

### Filename Requirements

When uploading files to the MASSO controller:
- **Maximum filename length**: 254 characters (255 keeps only a short alias on the controller, 256 gets no reply; observed on v5.13)
- **Supported characters**: ASCII only
- **Subdirectories**: Only if they already exist on the MASSO
- **Path separator**: Use backslash `\` for subdirectories, not forward slash `/`

Files with names longer than 254 characters will be automatically skipped in watch mode.

## Debug Mode

The `--debug` flag enables comprehensive packet logging for protocol analysis and troubleshooting:

### What Debug Mode Shows
- **All received packets**: Raw hex data, packet size, and source address
- **Packet filtering**: Automatically filters out repetitive keepalive responses and duplicate status packets
- **Protocol analysis**: Shows the complete packet flow for all operations

### Debug Output Example
```
[DEBUG] Packet 1: 46 bytes from ('192.168.1.32', 65535)
[DEBUG] Raw data: 7efb03000282160000000000404c617468652076352e3039006e63...
[DEBUG] Packet 2: 10 bytes from ('192.168.1.32', 65535)
[DEBUG] Raw data: f85c0300038216000000
[<] Configuration response received
    Controller Serial: 12345
```

### Use Cases
- **Protocol research**: Discover new packet types and structures
- **Troubleshooting**: Identify communication issues
- **Development**: Understand packet flow during operations
- **Verification**: Confirm packets are being sent/received correctly

### Filtering
Debug mode automatically filters out:
- Keepalive responses (repetitive 10-byte packets)
- Duplicate status packets (identical 270-byte packets)
- Shows only meaningful packet changes and important protocol messages

## Date Prefix Feature

The `--date-prefix` flag adds a short date prefix to uploaded filenames for easier identification on the MASSO device:

### Date Format
- **Format**: `MMDD-` (e.g., `1130-` for November 30th)
- **Purpose**: Easy chronological sorting and identification
- **Automatic**: Generated at upload time

### Usage Examples
```bash
# Manual upload with date prefix
python masso_udp_client.py --host <IP> --upload test.nc --date-prefix
# Result: "1130-test.nc"

# Watch mode with date prefix
python masso_udp_client.py --host <IP> --watch --date-prefix
# Auto-uploads as: "1130-filename.nc"

# Multiple files with date prefix
python masso_udp_client.py --host <IP> --upload file1.nc file2.nc --date-prefix
# Results: "1130-file1.nc", "1130-file2.nc"
```

### Smart Filename Handling
- **Length validation**: Respects the 254-character filename limit
- **Fallback behavior**: Uses original filename if prefix makes it too long (same for manual and watch uploads)
- **Folders**: Only the file part of a remote path is prefixed, e.g. `jobs\part.nc` becomes `jobs\0929-part.nc`
- **No double prefix**: A name that already starts with today's prefix is left alone
- **Clear feedback**: Shows when prefix is applied or skipped
- **Consistent behavior**: Works same for manual and watch uploads

### Example Output
```
[+] Detected new/changed NC file: part-program.nc
[-] Date prefix would make filename too long (255 chars, max 254)
    Using original filename: part-program.nc
[+] Auto-upload watching /path/to/files with date prefix (poll every 2.0s)
```

### Benefits
- **Easy identification**: See upload date at a glance
- **Chronological sorting**: Files sort by date on device
- **Version tracking**: Distinguish files uploaded on different days
- **Development workflow**: Perfect for automated uploads

### Mode Compatibility
- **✅ Manual upload**: `--upload` with `--date-prefix`
- **✅ Watch mode**: `--watch` with `--date-prefix`
- **❌ Interactive mode**: Manual filename control (no automation needed)

## Monitor Duration Control

The `--duration` parameter allows automated monitoring for testing and scripting:

### Usage Examples
```bash
# Monitor for 30 seconds, then auto-exit
python masso_udp_client.py --host 192.168.1.32 --monitor --duration 30

# Monitor with debug output for 10 seconds
python masso_udp_client.py --host 192.168.1.32 --monitor --duration 10 --debug

# Monitor with logging for 60 seconds
python masso_udp_client.py --host 192.168.1.32 --monitor --duration 60 --log
```

### Use Cases
- **Automated Testing**: Verify status monitoring without manual intervention
- **Scripted Operations**: Include monitoring in automated workflows
- **Data Collection**: Gather status data for specific time periods
- **Debugging**: Time-limited packet capture for troubleshooting

### Behavior
- Monitor starts and displays real-time status changes
- After specified duration, monitor automatically stops
- Client shuts down cleanly after monitoring completes
- Works with all other monitor options (--log, --debug)

## Feed Hold Detection

**Note**: This feature is experimental and based on limited protocol information. Detection may improve as more status data is discovered.

The client automatically detects feed hold conditions by monitoring:
- Line counter freezes while machine state is "Running"
- File state is "Executing"
- Duration exceeds configurable threshold (default 1.5 seconds)

## Tool Management

The client can request the complete tool list from the controller:
- Tools are requested progressively until an empty slot is found
- Tool names are decoded and displayed in a formatted table

## Device Information

- **IP Address**: User-specified (required via --host argument or connect command)
- **Protocol**: UDP-based proprietary with CRC16-CCITT checksum
- **Official Software**: MASSO Link (Windows, macOS, Linux)

## Files

- `masso_udp_client.py` - Interactive UDP client (primary tool)
- `PROTOCOL_SPEC.md` - Detailed protocol specification

## Contributing

If you discover any protocol details, please document them in `PROTOCOL_SPEC.md`!

## License

MIT
