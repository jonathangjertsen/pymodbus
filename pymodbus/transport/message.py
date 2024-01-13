"""ModbusMessage layer.

is extending ModbusProtocol to handle receiving and sending of messsagees.

ModbusMessage provides a unified interface to send/receive Modbus requests/responses.
"""
from __future__ import annotations

from enum import Enum

from pymodbus.logging import Log
from pymodbus.transport.transport import CommParams, ModbusProtocol


class CommFrameType(Enum):
    """Type of Modbus header."""

    RAW = 0
    SOCKET = 1
    TLS = 2
    RTU = 3
    ASCII = 4


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
        frameType: CommFrameType,
        params: CommParams,
        is_server: bool,
        ids: list[int],
    ) -> None:
        """Initialize a message instance.

        :param frameType: Modbus frame type
        :param params: parameter dataclass for protocol level
        :param is_server: true if object act as a server (listen/connect)
        :param ids: list of device id to accept, 0 for all.
        """
        self.ids = ids
        self.framerType: ModbusFrameType = {
            CommFrameType.RAW: ModbusFrameType(is_server),
            CommFrameType.SOCKET: FrameTypeSocket(is_server),
            CommFrameType.TLS: FrameTypeTLS(is_server),
            CommFrameType.RTU: FrameTypeRTU(is_server),
            CommFrameType.ASCII: FrameTypeASCII(is_server),
        }[frameType]
        super().__init__(params, is_server)


    def callback_data(self, data: bytes, _addr: tuple | None = None) -> int:
        """Handle call from protocol to collect frame."""
        if (datalen := len(data)) < self.framerType.min_len:
            return 0
        if not self.framerType.verifyFrame(data, datalen):
            return 0
        Log.debug("callback_data in message called: {}", data, ":hex")

        # add generic handling
        return 0


    # ---------------------------------------- #
    # callbacks / methods for external classes #
    # ---------------------------------------- #
    def callback_message(self, trans_id: int, dev_id: int, data: bytes) -> None:
        """Handle received data."""
        Log.debug("callback_message called: tid({}) dev_id({}) {}", trans_id, dev_id, data, ":hex")


    def message_send(self, trans_id: int, dev_id: int, data: bytes, addr: tuple | None = None) -> None:
        """Send request.

        :param trans_id: transaction id.
        :param dev_id: device id.
        :param data: non-empty bytes object with data to send.
        :param addr: optional addr, only used for UDP server.
        """
        send_data = self.framerType.build(trans_id, dev_id, data)
        self.transport_send(send_data, addr=addr)


class ModbusFrameType:
    """Generic header."""

    min_len: int = 0


    def __init__(self, is_server: bool) -> None:
        """Prepare frame handling."""
        self.slave = 0
        self.cutlen = 0
        self.dataStart = 0
        self.datalen = 0
        self.is_server = is_server


    def verifyFrame(self, data: bytes, datalen: int) -> bool:
        """Verify frame header is correct, return length."""
        self.slave = data[0]
        self.cutlen = datalen
        self.datalen = datalen
        self.dataStart = 0
        return True


    def build(self, _trans_id: int, _dev_id: int, data: bytes) -> bytes:
        """Build packet to send."""
        return data


class FrameTypeSocket(ModbusFrameType):
    """Modbus Socket frame type.

    [         MBAP Header         ] [ Function Code] [ Data ]
    [ tid ][ pid ][ length ][ uid ]
      2b     2b     2b        1b           1b           Nb

    * length = uid + function code + data
    """

    min_len: int = 9


    def verifyFrame(self, data: bytes, datalen: int) -> bool:
        """Verify frame header is correct, return length."""
        self.slave = data[0]
        self.cutlen = datalen
        self.datalen = datalen
        self.dataStart = 0
        return True


    def build(self, _trans_id: int, _dev_id: int, data: bytes) -> bytes:
        """Build packet to send."""
        return data


class FrameTypeTLS(ModbusFrameType):
    """Modbus TLS frame type.

    [ Function Code] [ Data ]
      1b               Nb
    """

    min_len: int = 2


    def verifyFrame(self, data: bytes, datalen: int) -> bool:
        """Verify frame header is correct, return length."""
        self.slave = data[0]
        self.cutlen = datalen
        self.datalen = datalen
        self.dataStart = 0
        return True


    def build(self, _trans_id: int, _dev_id: int, data: bytes) -> bytes:
        """Build packet to send."""
        return data


class FrameTypeRTU(ModbusFrameType):
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


    def verifyFrame(self, data: bytes, datalen: int) -> bool:
        """Verify frame header is correct, return length."""
        self.slave = data[0]
        self.cutlen = datalen
        self.datalen = datalen
        self.dataStart = 0
        return True


    def build(self, _trans_id: int, _dev_id: int, data: bytes) -> bytes:
        """Build packet to send."""
        return data


class FrameTypeASCII(ModbusFrameType):
    """Modbus ASCII Frame Controller.

    [ Start ][Address ][ Function ][ Data ][ LRC ][ End ]
      1c        2c         2c         Nc     2c      2c

    * data can be 0 - 2x252 ASCII chars
    * end is Carriage and return line feed, however the line feed
      character can be changed via a special command
    * start is ":"
    """

    min_len: int = 9


    def verifyFrame(self, data: bytes, datalen: int) -> bool:
        """Verify frame header is correct, return length."""
        self.slave = data[0]
        self.cutlen = datalen
        self.datalen = datalen
        self.dataStart = 0
        return True


    def build(self, _trans_id: int, _dev_id: int, data: bytes) -> bytes:
        """Build packet to send."""
        return data
