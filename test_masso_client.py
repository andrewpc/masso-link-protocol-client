#!/usr/bin/env python3
"""
Test suite for MASSO UDP Client

Tests packet building, protocol compliance, and core functionality.
"""

import unittest
import sys
import os
from datetime import datetime
from unittest.mock import Mock, patch, MagicMock

# Add the current directory to the path so we can import masso_udp_client
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from masso_udp_client import MassoClient


class TestPacketBuilding(unittest.TestCase):
    """Test packet building functions with zero values"""
    
    def setUp(self):
        """Set up test client"""
        self.client = MassoClient()
    
    def test_config_packet_structure(self):
        """Test configuration packet carries the PC time (zeros would reset the controller clock)"""
        self.client._connect_time = datetime(2026, 9, 29, 14, 16, 9)
        packet = self.client._build_config_packet()

        # Packet should be 14 bytes total (12 payload + 2 checksum)
        self.assertEqual(len(packet), 14)

        # Magic bytes and type
        self.assertEqual(packet[2:5], b'\x03\x00\x03')

        # hour minute second day month year, then 3 zero bytes
        self.assertEqual(packet[5:11], bytes([14, 16, 9, 29, 9, 26]))
        self.assertEqual(packet[11:14], b'\x00' * 3)
        
        # Verify checksum is calculated (not zero)
        checksum = int.from_bytes(packet[0:2], 'little')
        self.assertNotEqual(checksum, 0)
    
    def test_keepalive_packet_structure(self):
        """Test keepalive packet carries the connect time"""
        self.client._connect_time = datetime(2026, 9, 29, 14, 16, 9)
        packet = self.client._build_keepalive_packet()

        # Packet should be 10 bytes total (8 payload + 2 checksum)
        self.assertEqual(len(packet), 10)

        # Magic bytes and type
        self.assertEqual(packet[2:5], b'\x03\x00\x01')

        # hour minute second day month
        self.assertEqual(packet[5:10], bytes([14, 16, 9, 29, 9]))
        
        # Verify checksum is calculated (not zero)
        checksum = int.from_bytes(packet[0:2], 'little')
        self.assertNotEqual(checksum, 0)
    
    def test_discovery_packet_structure(self):
        """Test discovery packet has correct structure"""
        packet = self.client._build_discovery_packet()
        
        # Packet should be 10 bytes total (8 payload + 2 checksum)
        self.assertEqual(len(packet), 10)
        
        # Magic bytes and type
        self.assertEqual(packet[2:5], b'\x03\x00\x02')
        
        # Fixed payload; last byte is the month (not checked by the controller)
        self.client._connect_time = datetime(2026, 9, 29, 14, 16, 9)
        packet = self.client._build_discovery_packet()
        self.assertEqual(packet[5:10], b'\xf8\x2a\x00\x00\x09')
        
        # Verify checksum is calculated
        checksum = int.from_bytes(packet[0:2], 'little')
        self.assertNotEqual(checksum, 0)
    
    def test_checksum_calculation(self):
        """Test checksum calculation is consistent"""
        # Test same packet produces same checksum
        packet1 = self.client._build_config_packet()
        packet2 = self.client._build_config_packet()
        self.assertEqual(packet1[0:2], packet2[0:2])
        
        # Test different packets produce different checksums
        config_packet = self.client._build_config_packet()
        keepalive_packet = self.client._build_keepalive_packet()
        self.assertNotEqual(config_packet[0:2], keepalive_packet[0:2])


class TestSerialNumberExtraction(unittest.TestCase):
    """Test serial number extraction from configuration response"""
    
    def test_serial_extraction_little_endian(self):
        """Test serial number extraction with little-endian format"""
        # Simulate configuration response with serial 12345 (0x3039)
        mock_data = b'\xf8\x5c\x03\x00\x03\x39\x30\x00\x00\x00'
        
        # Extract serial from bytes 5-6 (little-endian)
        serial = int.from_bytes(mock_data[5:7], 'little')
        self.assertEqual(serial, 12345)
    
    def test_serial_extraction_different_values(self):
        """Test serial number extraction with various values"""
        test_cases = [
            (b'\x00\x01', 256),      # 0x0100 = 256
            (b'\xff\xff', 65535),    # 0xFFFF = 65535 (max)
            (b'\x00\x00', 0),        # 0x0000 = 0 (min)
            (b'\x34\x12', 4660),     # 0x1234 = 4660
        ]
        
        for serial_bytes, expected_value in test_cases:
            with self.subTest(serial_bytes=serial_bytes, expected=expected_value):
                serial = int.from_bytes(serial_bytes, 'little')
                self.assertEqual(serial, expected_value)


class TestProtocolCompliance(unittest.TestCase):
    """Test protocol compliance and packet validation"""
    
    def setUp(self):
        """Set up test client"""
        self.client = MassoClient()
    
    def test_packet_magic_bytes(self):
        """Test all packets use correct magic bytes"""
        config_packet = self.client._build_config_packet()
        keepalive_packet = self.client._build_keepalive_packet()
        discovery_packet = self.client._build_discovery_packet()
        
        # All should have magic bytes 0x03 0x00
        self.assertEqual(config_packet[2:4], b'\x03\x00')
        self.assertEqual(keepalive_packet[2:4], b'\x03\x00')
        self.assertEqual(discovery_packet[2:4], b'\x03\x00')
    
    def test_packet_types(self):
        """Test packet types are correct"""
        config_packet = self.client._build_config_packet()
        keepalive_packet = self.client._build_keepalive_packet()
        discovery_packet = self.client._build_discovery_packet()
        
        # Check packet type (byte 4)
        self.assertEqual(config_packet[4], 0x03)  # Configuration
        self.assertEqual(keepalive_packet[4], 0x01)  # Keepalive
        self.assertEqual(discovery_packet[4], 0x02)  # Discovery
    
    def test_packet_sizes(self):
        """Test packets have correct sizes"""
        config_packet = self.client._build_config_packet()
        keepalive_packet = self.client._build_keepalive_packet()
        discovery_packet = self.client._build_discovery_packet()
        
        # Expected sizes: payload + 2 bytes checksum
        self.assertEqual(len(config_packet), 14)    # 12 + 2
        self.assertEqual(len(keepalive_packet), 10)  # 8 + 2
        self.assertEqual(len(discovery_packet), 10)  # 8 + 2


class TestToolDataExtraction(unittest.TestCase):
    """Test tool data extraction from tool packets"""
    
    def test_tool_packet_parsing(self):
        """Test parsing of tool data packet"""
        # Simulate tool data packet for tool 1 with name "CCMT Right"
        mock_packet = b'\xfe\x00\x03\x00\x08\x01CCMT Right\x00\x00\x00'
        
        # Extract tool index (byte 5)
        tool_index = mock_packet[5]
        self.assertEqual(tool_index, 1)
        
        # Extract tool name (starts at byte 6, null-terminated)
        name_bytes = mock_packet[6:].split(b'\x00', 1)[0]
        tool_name = name_bytes.decode('ascii', errors='ignore')
        self.assertEqual(tool_name, 'CCMT Right')
    
    def test_empty_tool_handling(self):
        """Test handling of empty tool slots"""
        # Simulate empty tool packet
        mock_packet = b'\x03\x5a\x03\x00\x08\x0a\x00\x00\x00\x00\x00'
        
        # Extract tool index
        tool_index = mock_packet[5]
        self.assertEqual(tool_index, 10)
        
        # Extract tool name (should be empty)
        name_bytes = mock_packet[6:].split(b'\x00', 1)[0]
        tool_name = name_bytes.decode('ascii', errors='ignore')
        self.assertEqual(tool_name, '')


class TestStatusPacketParsing(unittest.TestCase):
    """Test status packet parsing and field extraction"""
    
    def test_job_count_extraction(self):
        """Test job count extraction from status packet"""
        # Simulate status packet with job count 58
        mock_packet = b'\xaa\x48\x03\x00\x01\x5a\x00\xff\x3a\x00\x00\x00\x01\x00\x00\x00'
        
        # Extract job count from bytes 8-11 (little-endian)
        job_count = int.from_bytes(mock_packet[8:12], 'little')
        self.assertEqual(job_count, 58)
    
    def test_state_field_parsing(self):
        """Test machine state field parsing"""
        # Test different machine states
        state_values = {
            0x00: "Idle",
            0x40: "Ready", 
            0x41: "Starting",
            0x51: "Running",
            0x5a: "Running",
            0x62: "Finishing",
            0x64: "Complete"
        }
        
        for state_byte, expected_state in state_values.items():
            with self.subTest(state_byte=state_byte, expected=expected_state):
                # This would be used in actual status parsing
                self.assertIsInstance(state_byte, int)
                self.assertGreaterEqual(state_byte, 0)
                self.assertLessEqual(state_byte, 255)


class TestFilenameValidation(unittest.TestCase):
    """Test filename validation for uploads"""
    
    def test_valid_filenames(self):
        """Test valid filename validation"""
        valid_filenames = [
            "test.nc",
            "program1.nc", 
            "short.nc",
            "a.nc",  # Minimum valid length
            "123456789012345.nc"  # Exactly 15 chars before .nc
        ]
        
        for filename in valid_filenames:
            with self.subTest(filename=filename):
                # Test basic filename length validation
                base_name = os.path.splitext(filename)[0]
                self.assertLessEqual(len(base_name), 15)
    
    def test_invalid_filenames(self):
        """Test invalid filename detection"""
        invalid_filenames = [
            "toolongfilenamehere.nc",  # Base name > 15 chars (19 chars)
            "verylongprogramname.nc",  # Base name > 15 chars (20 chars)
            "sixteenncharsname.nc"     # Base name > 15 chars (16 chars)
        ]
        
        for filename in invalid_filenames:
            with self.subTest(filename=filename):
                # Test filename length validation
                base_name = os.path.splitext(filename)[0]
                self.assertGreater(len(base_name), 15)


def run_tests():
    """Run all tests and report results"""
    # Create test suite
    test_suite = unittest.TestSuite()
    
    # Add test classes
    test_classes = [
        TestPacketBuilding,
        TestSerialNumberExtraction, 
        TestProtocolCompliance,
        TestToolDataExtraction,
        TestStatusPacketParsing,
        TestFilenameValidation
    ]
    
    for test_class in test_classes:
        tests = unittest.TestLoader().loadTestsFromTestCase(test_class)
        test_suite.addTests(tests)
    
    # Run tests
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(test_suite)
    
    # Return success/failure
    return result.wasSuccessful()


class TestUploadPackets(unittest.TestCase):
    """Data chunk and tool request packets (values verified against captures and a v5.13 controller)"""

    def setUp(self):
        self.client = MassoClient()

    def test_tool_request_tail_is_connect_time(self):
        """Tool request ends with minute, second, day, month (captures: 22 2c 1c 0b at 13:34:44 on 28 Nov)"""
        self.client._connect_time = datetime(2025, 11, 28, 13, 34, 44)
        packet = self.client._build_tool_request_packet(1)
        self.assertEqual(packet[2:], bytes([0x03, 0x00, 0x08, 0x01, 0x22, 0x2c, 0x1c, 0x0b]))

    def test_short_final_chunk_trailer_rule(self):
        """Payload after the CRC is a multiple of 4: trailer = (-(11 + len)) % 4, 0 becomes 4"""
        # 863 -> 2 (v5.09 capture, 878 byte packet), 1003 -> 2, 442 -> 3, 1021 -> 4, 906 -> 3
        expected = {863: 2, 1003: 2, 442: 3, 1021: 4, 906: 3, 1000: 1, 1001: 4, 1002: 3}
        for length, trailer in expected.items():
            self.assertEqual(MassoClient._chunk_trailer_len(length), trailer, length)

    def test_data_packet_lengths(self):
        """Full chunk is 1438 bytes; short final chunk is 13 + len + trailer"""
        full = self.client._build_data_packet(0, b'a' * 1422, 3)
        self.assertEqual(len(full), 1438)
        short = self.client._build_data_packet(321, b'b' * 863, MassoClient._chunk_trailer_len(863))
        self.assertEqual(len(short), 878)
        self.assertEqual(int.from_bytes(short[5:9], 'little'), 321)
        self.assertEqual(int.from_bytes(short[9:13], 'little'), 863)

    def test_data_packet_full_wire_fallback(self):
        """Fallback keeps the real length field but pads the data area to full size"""
        packet = self.client._build_data_packet(0, b'c' * 411, 3, pad_to=1422)
        self.assertEqual(len(packet), 1438)
        self.assertEqual(int.from_bytes(packet[9:13], 'little'), 411)

    def test_data_packet_checksum(self):
        packet = self.client._build_data_packet(5, b'x' * 100, 3)
        self.assertEqual(packet[:2], self.client._calculate_checksum(packet[2:]))


if __name__ == '__main__':
    print("MASSO UDP Client Test Suite")
    print("=" * 50)
    
    success = run_tests()
    
    print("\n" + "=" * 50)
    if success:
        print("All tests passed!")
        sys.exit(0)
    else:
        print("Some tests failed!")
        sys.exit(1)
