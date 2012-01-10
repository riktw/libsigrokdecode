##
## This file is part of the sigrok project.
##
## Copyright (C) 2010-2011 Uwe Hermann <uwe@hermann-uwe.de>
##
## This program is free software; you can redistribute it and/or modify
## it under the terms of the GNU General Public License as published by
## the Free Software Foundation; either version 2 of the License, or
## (at your option) any later version.
##
## This program is distributed in the hope that it will be useful,
## but WITHOUT ANY WARRANTY; without even the implied warranty of
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
## GNU General Public License for more details.
##
## You should have received a copy of the GNU General Public License
## along with this program; if not, write to the Free Software
## Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301 USA
##

#
# I2C protocol decoder
#

#
# The Inter-Integrated Circuit (I2C) bus is a bidirectional, multi-master
# bus using two signals (SCL = serial clock line, SDA = serial data line).
#
# There can be many devices on the same bus. Each device can potentially be
# master or slave (and that can change during runtime). Both slave and master
# can potentially play the transmitter or receiver role (this can also
# change at runtime).
#
# Possible maximum data rates:
#  - Standard mode: 100 kbit/s
#  - Fast mode: 400 kbit/s
#  - Fast-mode Plus: 1 Mbit/s
#  - High-speed mode: 3.4 Mbit/s
#
# START condition (S): SDA = falling, SCL = high
# Repeated START condition (Sr): same as S
# Data bit sampling: SCL = rising
# STOP condition (P): SDA = rising, SCL = high
#
# All data bytes on SDA are exactly 8 bits long (transmitted MSB-first).
# Each byte has to be followed by a 9th ACK/NACK bit. If that bit is low,
# that indicates an ACK, if it's high that indicates a NACK.
#
# After the first START condition, a master sends the device address of the
# slave it wants to talk to. Slave addresses are 7 bits long (MSB-first).
# After those 7 bits, a data direction bit is sent. If the bit is low that
# indicates a WRITE operation, if it's high that indicates a READ operation.
#
# Later an optional 10bit slave addressing scheme was added.
#
# Documentation:
# http://www.nxp.com/acrobat/literature/9398/39340011.pdf (v2.1 spec)
# http://www.nxp.com/acrobat/usermanuals/UM10204_3.pdf (v3 spec)
# http://en.wikipedia.org/wiki/I2C
#

# TODO: Look into arbitration, collision detection, clock synchronisation, etc.
# TODO: Handle clock stretching.
# TODO: Handle combined messages / repeated START.
# TODO: Implement support for 7bit and 10bit slave addresses.
# TODO: Implement support for inverting SDA/SCL levels (0->1 and 1->0).
# TODO: Implement support for detecting various bus errors.
# TODO: I2C address of slaves.
# TODO: Handle multiple different I2C devices on same bus
#       -> we need to decode multiple protocols at the same time.

#
# I2C protocol output format:
#
# The protocol output consists of a (Python) list of I2C "packets", each of
# which is of the form
#
#        [ _i2c_command_, _data_, _ack_bit_ ]
#
# _i2c_command_ is one of:
#   - 'START' (START condition)
#   - 'START_REPEAT' (Repeated START)
#   - 'ADDRESS_READ' (Address, read)
#   - 'ADDRESS_WRITE' (Address, write)
#   - 'DATA_READ' (Data, read)
#   - 'DATA_WRITE' (Data, write)
#   - 'STOP' (STOP condition)
#
# _data_ is the data or address byte associated with the ADDRESS_* and DATA_*
# command. For START, START_REPEAT and STOP, this is None.
#
# _ack_bit_ is either 'ACK' or 'NACK', but may also be None.
#
#

import sigrokdecode

# annotation feed formats
ANN_SHIFTED       = 0
ANN_SHIFTED_SHORT = 1
ANN_RAW           = 2

# values are verbose and short annotation, respectively
protocol = {
    'START':           ['START',        'S'],
    'START_REPEAT':    ['START REPEAT', 'Sr'],
    'STOP':            ['STOP',         'P'],
    'ACK':             ['ACK',          'A'],
    'NACK':            ['NACK',         'N'],
    'ADDRESS_READ':    ['ADDRESS READ', 'AR'],
    'ADDRESS_WRITE':   ['ADDRESS WRITE','AW'],
    'DATA_READ':       ['DATA READ',    'DR'],
    'DATA_WRITE':      ['DATA WRITE',   'DW'],
}

# States
FIND_START = 0
FIND_ADDRESS = 1
FIND_DATA = 2


class Decoder(sigrokdecode.Decoder):
    id = 'i2c'
    name = 'I2C'
    longname = 'Inter-Integrated Circuit (I2C) bus'
    desc = 'I2C is a two-wire, multi-master, serial bus.'
    longdesc = '...'
    author = 'Uwe Hermann'
    email = 'uwe@hermann-uwe.de'
    license = 'gplv2+'
    inputs = ['logic']
    outputs = ['i2c']
    probes = [
        {'id': 'scl', 'name': 'SCL', 'desc': 'Serial clock line'},
        {'id': 'sda', 'name': 'SDA', 'desc': 'Serial data line'},
    ]
    options = {
        'address-space': ['Address space (in bits)', 7],
    }
    annotation = [
        # ANN_SHIFTED
        ["7-bit shifted hex",
         "Read/Write bit shifted out from the 8-bit i2c slave address"],
        # ANN_SHIFTED_SHORT
        ["7-bit shifted hex (short)",
         "Read/Write bit shifted out from the 8-bit i2c slave address"],
        # ANN_RAW
        ["Raw hex", "Unaltered raw data"]
    ]

    def __init__(self, **kwargs):
        self.out_proto = None
        self.out_ann = None
        self.samplecnt = 0
        self.bitcount = 0
        self.databyte = 0
        self.wr = -1
        self.startsample = -1
        self.is_repeat_start = 0
        self.state = FIND_START
        self.oldscl = None
        self.oldsda = None

    def start(self, metadata):
        self.out_proto = self.add(sigrokdecode.SRD_OUTPUT_PROTOCOL, 'i2c')
        self.out_ann = self.add(sigrokdecode.SRD_OUTPUT_ANNOTATION, 'i2c')

    def report(self):
        pass

    def is_start_condition(self, scl, sda):
        """START condition (S): SDA = falling, SCL = high"""
        if (self.oldsda == 1 and sda == 0) and scl == 1:
            return True
        return False

    def is_data_bit(self, scl, sda):
        """Data sampling of receiver: SCL = rising"""
        if self.oldscl == 0 and scl == 1:
            return True
        return False

    def is_stop_condition(self, scl, sda):
        """STOP condition (P): SDA = rising, SCL = high"""
        if (self.oldsda == 0 and sda == 1) and scl == 1:
            return True
        return False

    def found_start(self, scl, sda):
        if self.is_repeat_start == 1:
            cmd = 'START_REPEAT'
        else:
            cmd = 'START'
        self.put(self.out_proto, [ cmd, None, None ])
        self.put(self.out_ann, [ ANN_SHIFTED, [protocol[cmd][0]] ])
        self.put(self.out_ann, [ ANN_SHIFTED_SHORT, [protocol[cmd][1]] ])

        self.state = FIND_ADDRESS
        self.bitcount = self.databyte = 0
        self.is_repeat_start = 1
        self.wr = -1

    def found_address_or_data(self, scl, sda):
        """Gather 8 bits of data plus the ACK/NACK bit."""

        if self.startsample == -1:
            # TODO: should be samplenum, as received from the feed
            self.startsample = self.samplecnt
        self.bitcount += 1

        # Address and data are transmitted MSB-first.
        self.databyte <<= 1
        self.databyte |= sda

        # Return if we haven't collected all 8 + 1 bits, yet.
        if self.bitcount != 9:
            return []

        # send raw output annotation before we start shifting out
        # read/write and ack/nack bits
        self.put(self.out_ann, [ANN_RAW, ["0x%.2x" % self.databyte]])

        # We received 8 address/data bits and the ACK/NACK bit.
        self.databyte >>= 1 # Shift out unwanted ACK/NACK bit here.

        if self.state == FIND_ADDRESS:
            # The READ/WRITE bit is only in address bytes, not data bytes.
            if self.databyte & 1:
                self.wr = 0
            else:
                self.wr = 1
            d = self.databyte >> 1
        elif self.state == FIND_DATA:
            d = self.databyte
        else:
            # TODO: Error?
            pass

        # last bit that came in was the ACK/NACK bit (1 = NACK)
        if sda == 1:
            ack_bit = 'NACK'
        else:
            ack_bit = 'ACK'

        # TODO: Simplify.
        if self.state == FIND_ADDRESS and self.wr == 1:
            cmd = 'ADDRESS_WRITE'
        elif self.state == FIND_ADDRESS and self.wr == 0:
            cmd = 'ADDRESS_READ'
        elif self.state == FIND_DATA and self.wr == 1:
            cmd = 'DATA_WRITE'
        elif self.state == FIND_DATA and self.wr == 0:
            cmd = 'DATA_READ'
        self.put(self.out_proto, [ cmd, d, ack_bit ] )
        self.put(self.out_ann, [ANN_SHIFTED, [
                "%s" % protocol[cmd][0],
                "0x%02x" % d,
                "%s" % protocol[ack_bit][0]]
            ] )
        self.put(self.out_ann, [ANN_SHIFTED_SHORT, [
                "%s" % protocol[cmd][1],
                "0x%02x" % d,
                "%s" % protocol[ack_bit][1]]
            ] )

        self.bitcount = self.databyte = 0
        self.startsample = -1

        if self.state == FIND_ADDRESS:
            self.state = FIND_DATA
        elif self.state == FIND_DATA:
            # There could be multiple data bytes in a row.
            # So, either find a STOP condition or another data byte next.
            pass

    def found_stop(self, scl, sda):
        self.put(self.out_proto, [ 'STOP', None, None ])
        self.put(self.out_ann, [ ANN_SHIFTED, [protocol['STOP'][0]] ])
        self.put(self.out_ann, [ ANN_SHIFTED_SHORT, [protocol['STOP'][1]] ])

        self.state = FIND_START
        self.is_repeat_start = 0
        self.wr = -1

    def put(self, output_id, data):
        # inject sample range into the call up to sigrok
        # TODO: 0-0 sample range for now
        super(Decoder, self).put(0, 0, output_id, data)

    def decode(self, timeoffset, duration, data):
        for samplenum, (scl, sda) in data:
            self.samplecnt += 1

            # First sample: Save SCL/SDA value.
            if self.oldscl == None:
                self.oldscl = scl
                self.oldsda = sda
                continue

            # TODO: Wait until the bus is idle (SDA = SCL = 1) first?

            # State machine.
            if self.state == FIND_START:
                if self.is_start_condition(scl, sda):
                    self.found_start(scl, sda)
            elif self.state == FIND_ADDRESS:
                if self.is_data_bit(scl, sda):
                    self.found_address_or_data(scl, sda)
            elif self.state == FIND_DATA:
                if self.is_data_bit(scl, sda):
                    self.found_address_or_data(scl, sda)
                elif self.is_start_condition(scl, sda):
                    self.found_start(scl, sda)
                elif self.is_stop_condition(scl, sda):
                    self.found_stop(scl, sda)
            else:
                # TODO: Error?
                pass

            # Save current SDA/SCL values for the next round.
            self.oldscl = scl
            self.oldsda = sda

