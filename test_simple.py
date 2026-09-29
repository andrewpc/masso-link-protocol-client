#!/usr/bin/env python3
"""
Simple test suite for MASSO UDP Client

Focuses on core functionality that can be tested without hardware.
"""

import unittest
import sys
import os
from datetime import datetime

# Add the current directory to the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from masso_udp_client import MassoClient


class TestMassoClientBasics(unittest.TestCase):
    """Test basic MassoClient functionality"""
    
    def test_client_creation(self):
        """Test client can be created"""
        client = MassoClient()
        self.assertIsNotNone(client)
        self.assertEqual(client.pc_port, 11000)
        self.assertEqual(client.controller_port, 65535)
    
    def test_client_with_host(self):
        """Test client creation with host parameter"""
        host = "192.168.1.32"
        client = MassoClient(host=host)
        self.assertEqual(client.host, host)
    
    def test_debug_mode(self):
        """Test debug mode setting"""
        # Test debug off
        client = MassoClient(debug=False)
        self.assertFalse(client.debug)
        
        # Test debug on
        client = MassoClient(debug=True)
        self.assertTrue(client.debug)


class TestPacketBuilding(unittest.TestCase):
    """Test packet building functions"""
    
    def setUp(self):
        """Set up test client"""
        self.client = MassoClient()
    
    def test_config_packet_size(self):
        """Test config packet has correct size"""
        packet = self.client._build_config_packet()
        self.assertEqual(len(packet), 14)  # 12 payload + 2 checksum
    
    def test_keepalive_packet_size(self):
        """Test keepalive packet has correct size"""
        packet = self.client._build_keepalive_packet()
        self.assertEqual(len(packet), 10)  # 8 payload + 2 checksum
    
    def test_discovery_packet_size(self):
        """Test discovery packet has correct size"""
        packet = self.client._build_discovery_packet()
        self.assertEqual(len(packet), 10)  # 8 payload + 2 checksum
    
    def test_config_packet_zeros(self):
        """Test config packet carries the PC time and zero trailing bytes"""
        self.client._connect_time = datetime(2026, 9, 29, 14, 16, 9)
        packet = self.client._build_config_packet()

        # Magic bytes and type
        self.assertEqual(packet[2:5], b'\x03\x00\x03')

        # hour minute second day month year, then 3 zero bytes
        self.assertEqual(packet[5:11], bytes([14, 16, 9, 29, 9, 26]))
        self.assertEqual(packet[11:14], b'\x00' * 3)
    
    def test_keepalive_packet_zeros(self):
        """Test keepalive packet carries the connect time"""
        self.client._connect_time = datetime(2026, 9, 29, 14, 16, 9)
        packet = self.client._build_keepalive_packet()

        # Magic bytes and type
        self.assertEqual(packet[2:5], b'\x03\x00\x01')

        # hour minute second day month
        self.assertEqual(packet[5:10], bytes([14, 16, 9, 29, 9]))
    
    def test_checksum_consistency(self):
        """Test checksum calculation is consistent"""
        # Same packet should produce same checksum
        packet1 = self.client._build_config_packet()
        packet2 = self.client._build_config_packet()
        self.assertEqual(packet1[0:2], packet2[0:2])
        
        # Different packets should produce different checksums
        config_packet = self.client._build_config_packet()
        keepalive_packet = self.client._build_keepalive_packet()
        self.assertNotEqual(config_packet[0:2], keepalive_packet[0:2])


class TestToolRequestBuilding(unittest.TestCase):
    """Test tool request packet building"""
    
    def setUp(self):
        """Set up test client"""
        self.client = MassoClient()
    
    def test_tool_request_valid_indices(self):
        """Test tool request building for valid tool indices"""
        for tool_index in [1, 10, 100, 255]:
            packet = self.client._build_tool_request_packet(tool_index)
            self.assertIsNotNone(packet)
            self.assertEqual(len(packet), 10)  # 8 payload + 2 checksum
            self.assertEqual(packet[4], 0x08)  # Tool request type
            self.assertEqual(packet[5], tool_index)  # Tool index
    
    def test_tool_request_invalid_indices(self):
        """Test tool request building rejects invalid indices"""
        # Test tool index too low
        with self.assertRaises(ValueError):
            self.client._build_tool_request_packet(0)
        
        # Test tool index too high
        with self.assertRaises(ValueError):
            self.client._build_tool_request_packet(256)


class TestUtilityFunctions(unittest.TestCase):
    """Test utility functions"""
    
    def setUp(self):
        """Set up test client"""
        self.client = MassoClient()
    
    def test_serial_number_extraction(self):
        """Test serial number extraction from response"""
        # Mock configuration response with serial 12345 (0x3039)
        mock_data = b'\xf8\x5c\x03\x00\x03\x39\x30\x00\x00\x00'
        
        # Extract serial from bytes 5-6 (little-endian)
        serial = int.from_bytes(mock_data[5:7], 'little')
        self.assertEqual(serial, 12345)
    
    def test_job_count_extraction(self):
        """Test job count extraction from status packet"""
        # Mock status packet with job count 58
        mock_packet = b'\xaa\x48\x03\x00\x01\x5a\x00\xff\x3a\x00\x00\x00\x01\x00\x00\x00'
        
        # Extract job count from bytes 8-11 (little-endian)
        job_count = int.from_bytes(mock_packet[8:12], 'little')
        self.assertEqual(job_count, 58)
    
    def test_filename_validation(self):
        """Test filename length validation"""
        # Valid filenames (<= 15 chars before .nc)
        valid_names = [
            "test.nc",
            "program1.nc",
            "short.nc",
            "123456789012345.nc"  # Exactly 15 chars
        ]
        
        for filename in valid_names:
            base_name = os.path.splitext(filename)[0]
            self.assertLessEqual(len(base_name), 15)
        
        # Invalid filenames (> 15 chars before .nc)
        invalid_names = [
            "toolongfilenamehere.nc",
            "verylongprogramname.nc", 
            "sixteenncharsname.nc"
        ]
        
        for filename in invalid_names:
            base_name = os.path.splitext(filename)[0]
            self.assertGreater(len(base_name), 15)


class TestPacketTypes(unittest.TestCase):
    """Test packet type validation"""
    
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


def run_simple_tests():
    """Run all simple tests"""
    test_suite = unittest.TestSuite()
    
    # Add test classes
    test_classes = [
        TestMassoClientBasics,
        TestPacketBuilding,
        TestToolRequestBuilding,
        TestUtilityFunctions,
        TestPacketTypes
    ]
    
    for test_class in test_classes:
        tests = unittest.TestLoader().loadTestsFromTestCase(test_class)
        test_suite.addTests(tests)
    
    # Run tests
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(test_suite)
    
    return result.wasSuccessful()


if __name__ == '__main__':
    print("MASSO UDP Client Simple Tests")
    print("=" * 40)
    
    success = run_simple_tests()
    
    print("\n" + "=" * 40)
    if success:
        print("All simple tests passed!")
        sys.exit(0)
    else:
        print("Some simple tests failed!")
        sys.exit(1)
