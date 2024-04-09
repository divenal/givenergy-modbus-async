#!/usr/bin/env python3

# Read a file containing a log of modbus activity.
# This can either be from a man-in-the-middle logger
# (ie listen on 8899, connect to real device, then pass everything through, recording to a file)
# or just a new connect which just records all traffic (exploiting the fact that the
# givenergy dongle appears to broadcast all modbus messages to all connected tcp clients).
# eg can use socat
#   socat -x -r binfile TCP-LISTEN:8899 TCP:host:8899   # in the middle
#   socat -x -u TCP:host:8899 CREATE:binfile            # spy

import asyncio
import logging
import socket
import sys
#from asyncio import Future, Queue, StreamReader, StreamWriter, Task
from typing import Callable, Dict, List, Optional, Tuple

from givenergy_modbus.exceptions import CommunicationError, ExceptionBase
from givenergy_modbus.framer import ClientFramer, Framer
from givenergy_modbus.pdu import BasePDU
from givenergy_modbus.codec import PayloadDecoder
from givenergy_modbus.model.plant import Plant

_logger = logging.getLogger(__name__)

def replay():
    """Read modbus frames from a file"""

    framer = ClientFramer()
    plant = Plant()

    log = open(sys.argv[1], mode='rb')
    while len(frame := log.read(300)) > 0:
        try:
            for message in framer.decode(frame):
                _logger.info(f'Processing {message}')
                plant.update(message)
        except (ExceptionBase, NotImplementedError) as e:
            print(e)
        # print(plant)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    replay()
