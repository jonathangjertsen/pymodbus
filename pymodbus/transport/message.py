"""ModbusMessage layer.

is extending ModbusProtocol to handle receiving and sending of messsagees.

ModbusMessage provides a unified interface to send/receive Modbus requests/responses.
"""
from __future__ import annotations

import struct
from enum import Enum

from pymodbus.logging import Log
from pymodbus.transport.transport import CommParams, ModbusProtocol


class FrameType(str, Enum):
    """Type of Modbus framer."""

    RAW = "raw"  # only used for testing
    ASCII = "ascii"
    RTU = "rtu"
    SOCKET = "socket"
    TLS = "tls"


class ModbusMessage(ModbusProtocol):
    """Message layer extending transport layer.

    When receiving:
    - Secures full valid Modbus message is received (across multiple callbacks)
    - Validates and removes Modbus header (CRC for serial, MBAP for others)
    - Decodes frame according to frame type
    - Callback with pure request/response
    - Skips invalid frames (retry)

    When sending:
    - Encode request/response according to frame type
    - Generate Modbus message by adding header (CRC for serial, MBAP for others)
    - Call transport to do the actual sending of data

    The class is designed to take care of differences between the different modbus headers,
    and provide a neutral interface for the upper layers.
    """

    def __init__(
        self,
        frameType: FrameType,
        params: CommParams,
        is_server: bool,
        dev_ids: list[int] | None,
    ) -> None:
        """Initialize a message instance.

        :param frameType: Modbus frame type
        :param params: parameter dataclass for protocol level
        :param is_server: true if object act as a server (listen/connect)
        :param dev_ids: list of device id to accept (server only), None for all.
        """
        self.dev_ids = dev_ids
        self.framer: FrameRaw = {
            FrameType.RAW: FrameRaw(is_server),
            FrameType.ASCII: FrameAscii(is_server),
            FrameType.RTU: FrameRTU(is_server),
            FrameType.SOCKET: FrameSocket(is_server),
            FrameType.TLS: FrameTLS(is_server),
        }[frameType]
        super().__init__(params, is_server)


    def callback_data(self, data: bytes, _addr: tuple | None = None) -> int:
        """Handle call from protocol to collect frame."""
        if not self.framer.verifyFrame(data):
            return 0
        dev_id, req_resp, used_len = self.framer.getMessage(data)
        if self.dev_ids and dev_id not in self.dev_ids:
            Log.debug("skipping request/response from non defined dev_id({}) {}", dev_id, data, ":hex")
        else:
            Log.debug("request/response received from dev_id({}) {}", dev_id, data, ":hex")
            self.callback_message(dev_id, req_resp)
        return used_len


    # ---------------------------------------- #
    # callbacks / methods for external classes #
    # ---------------------------------------- #
    def reset(self) -> None:
        """Reset frame."""
        self.framer.reset()

    def callback_message(self, dev_id: int, data: bytes) -> None:
        """Handle received data."""
        Log.debug("callback_message called: dev_id({}) {}", dev_id, data, ":hex")


    def message_send(self, dev_id: int, data: bytes, addr: tuple | None = None) -> None:
        """Send request.

        :param dev_id: device id.
        :param data: non-empty bytes object with data to send.
        :param addr: optional IP addr, only used for UDP server.
        """
        Log.debug("Request/response to be sent to dev_id({}) {}", dev_id, data, ":hex")
        send_data = self.framer.build(dev_id, data)
        self.transport_send(send_data, addr=addr)


class FrameRaw:
    """Generic header.

    HEADER:
        byte[0] = dev_id
        byte[1-2] = length of request/response, NOT converted
        byte[3..] = request/response
    """

    MIN_LEN = 5  # Header 3 bytes + Minimum modbus message 2 bytes


    def __init__(self, is_server: bool) -> None:
        """Prepare frame handling."""
        self.is_server = is_server
        self.data_len = 0
        self.msg_len = 0
        self.dev_id = 0


    def reset(self) -> None:
        """Reset frame."""
        self.data_len = 0
        self.msg_len = 0
        self.dev_id = 0


    def verifyFrame(self, data: bytes) -> bool:
        """Verify frame header is correct, return length."""
        self.data_len = len(data)
        if self.data_len < self.MIN_LEN:
            return False
        self.dev_id, self.msg_len = struct.unpack(">BH", data)
        if self.data_len < self.msg_len + self.MIN_LEN:
            return False
        return True


    def build(self, dev_id: int, data: bytes) -> bytes:
        """Build packet to send."""
        header = struct.pack(">BH", dev_id, len(data))
        return header + data


    def getMessage(self, data: bytes):
        """Get modbus request/response."""
        return data[self.MIN_LEN:self.MIN_LEN+self.msg_len]


class FrameAscii(FrameRaw):
    """Modbus Socket frame type.

    [         MBAP Header         ] [ Function Code] [ Data ]
    [ tid ][ pid ][ length ][ uid ]
      2b     2b     2b        1b           1b           Nb

    * length = uid + function code + data
    """

    min_len: int = 9


    def verifyFrame(self, data: bytes) -> bool:
        """Verify frame header is correct, return length."""
        self.dev_id = data[0]
        return True


    def build(self, _dev_id: int, data: bytes) -> bytes:
        """Build packet to send."""
        return data


class FrameRTU(FrameRaw):
    """Modbus RTU frame type.

    [ Start Wait ] [Address ][ Function Code] [ Data ][ CRC ][  End Wait  ]
        3.5 chars     1b         1b               Nb      2b      3.5 chars

    Wait refers to the amount of time required to transmit at least x many
    characters.  In this case it is 3.5 characters.  Also, if we receive a
    wait of 1.5 characters at any point, we must trigger an error message.
    Also, it appears as though this message is little endian. The logic is
    simplified as the following::

    The following table is a listing of the baud wait times for the specified
    baud rates::

        ------------------------------------------------------------------
           Baud  1.5c (18 bits)   3.5c (38 bits)
        ------------------------------------------------------------------
           1200  15,000 ms        31,667 ms
           4800   3,750 ms         7,917 ms
           9600   1,875 ms         3,958 ms
          19200   0,938 ms         1,979 ms
          38400   0,469 ms         0,989 ms
         115200   0,156 ms         0,329 ms
        ------------------------------------------------------------------
        1 Byte = 8 bits + 1 bit parity + 2 stop bit = 11 bits

    * Note: due to the USB converter and the OS drivers, timing cannot be quaranteed
    neither when receiving nor when sending.
    """

    min_len: int = 4


    def verifyFrame(self, data: bytes) -> bool:
        """Verify frame header is correct, return length."""
        self.dev_id = data[0]
        return True


    def build(self, _dev_id: int, data: bytes) -> bytes:
        """Build packet to send."""
        return data


class FrameSocket(FrameRaw):
    """Modbus Socket frame type.

    [         MBAP Header         ] [ Function Code] [ Data ]
    [ tid ][ pid ][ length ][ uid ]
      2b     2b     2b        1b           1b           Nb

    * length = uid + function code + data
    """

    min_len: int = 9


    def verifyFrame(self, data: bytes) -> bool:
        """Verify frame header is correct, return length."""
        self.dev_id = data[0]
        return True


    def build(self, _dev_id: int, data: bytes) -> bytes:
        """Build packet to send."""
        return data


class FrameTLS(FrameRaw):
    """Modbus TLS frame type.

    [ Function Code] [ Data ]
      1b               Nb
    """

    min_len: int = 2


    def verifyFrame(self, data: bytes) -> bool:
        """Verify frame header is correct, return length."""
        self.dev_id = data[0]
        return True


    def build(self, _dev_id: int, data: bytes) -> bytes:
        """Build packet to send."""
        return data
