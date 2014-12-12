#!/usr/bin/env python3
#
# Skywriter HAT GestIC Firmware Updater
#
######################################################################
#
# FW_UPDATE_START
#
# Size | Flags | Seq | ID   | CRC | SessionID | IV | UpdateFn | Reserved
# 1      1       1     1    | 4     4           14   1          1
# 0x1c   n/a     n/a   0x80 | Desc below
#
# CRC
# ---
# CRC32, Ethernet. Polynomial 0x04C11DB7
# Calculated across remaining 20 bytes of message
# 32-bit word
#
# SessionID
# ---------
# Random number generated by host, must be present in FW_UPDATE_COMPLETED
# or session will be invalid.
#
# IV
# --
# 14-byte value used to encrypt the data
#
# UpdateFunction
# ---------------
# 0 - Program Flash
# 1 - Verify Only
# If set to verify at this stage, then subsequent block requests can *only*
# verify
#
# Reserved
# --------
# Reserved!
#
#####################################################################
#
# FW_UPDATE_BLOCK
# 
# Size   ...   ID | CRC | Address | Length | UpdateFunction | Payload
# 0x8c       0x81 | 4     2         1        1                128
#
# CRC
# ---
# Calculated over remainder of message ( 132 bytes )
#
# Address
# -------
# Flash address of block which will be programmed/verified
# Lower 4KB reserved by the library loader
# Range: (0x1000..0x7fff)
#
# Length
# ------
# Length of the content block which will be updated
# Range: (0x00..0x80)
#
# UpdateFunction
# --------------
# As above, 0 - Program, 1 - Verify
#
# Payload
# -------
# ALWAYS 128 bytes long. Remainder filled with zeros.
#
######################################################################
#
# FW_UPDATE_COMPLETED
#
# Size  ...   ID | CRC | SessionID | UpdateFunction | FwVersion | Reserved
# 8x88      0x82 | 4     4           1                120         3
#
# CRC
# ---
# Calculated over remaining 128 bytes
#
# SessionID
# ---------
# As above
#
# UpdateFunction
# --------------
# If it was started with ProgramFlash ( 0 ) then it must be finalised
# with ProgramFlash ( 0 ).
#
# 0 - Program Flash
# 1
# 3 - Restart ( Presumably if any verify stage fails )
#
# FwVersion
# ---------
# 120 bytes interpreted as a string, containing Firmware Version

import random, binascii
import fw
import i2c
import time
import RPi.GPIO as GPIO


SW_ADDR      = 0x42
SW_RESET_PIN = 17
SW_XFER_PIN  = 27

FW_UPDATE_START      = 0x80
FW_UPDATE_BLOCK      = 0x81
FW_UPDATE_COMPLETED  = 0x82

FW_UPDATE_FN_PROG    = 0
FW_UPDATE_FN_VERIFY  = 1
FW_UPDATE_FN_RESTART = 3

GPIO.setmode(GPIO.BCM)
GPIO.setup(SW_RESET_PIN, GPIO.OUT, initial=GPIO.HIGH)
GPIO.setup(SW_XFER_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)

class Skyware():
  session_id = 1
  i2c = None

  def i2c_bus_id(self):
    revision = ([l[12:-1] for l in open('/proc/cpuinfo','r').readlines() if l[:8]=="Revision"]+['0000'])[0]
    return 1 if int(revision, 16) >= 4 else 0

  def __init__(self):
    self.session_id = 123 #random.getrandbits(32)
    self.i2c = i2c.I2CMaster(self.i2c_bus_id())

  def i2c_write(self, data):
    #print('Writing packet:',len(data),data)
    self.i2c.transaction(i2c.writing_bytes(SW_ADDR, *data))

  def calculate_crc(self,payload):
    crc = binascii.crc32(bytes(payload))
    #print(crc, bytes(payload))
    return crc

  def update_begin(self, iv, verify_only = False):
    payload = Payload()
    payload.append(0x1c) # Length
    payload.append(0x00) # Flags
    payload.append(0x00) # Seq
    payload.append(FW_UPDATE_START)

    payload.append(0, 4) # Reserve 4 bytes for CRC
    
    payload.append(self.session_id, 4) # Session ID

    payload.append(iv) # 14 0s as encryption key

    if verify_only:
      payload.append(FW_UPDATE_FN_VERIFY)
    else:
      payload.append(FW_UPDATE_FN_PROG) # Set function to program

    payload.append(0) # Reserved

    crc = self.calculate_crc(payload[8:])
    payload.replace(4,crc,4)
 
    self.i2c_write(payload)
    time.sleep(0.04)
    
    # Wait for start confirmation
    ts = int(round(time.time() * 1000))
    while int(round(time.time() * 1000)) < (ts + 10000):
      result = self.handle_exception()
      if result[4] == FW_UPDATE_START and result[6] == 0:
        return True  # Start success
      elif result[4] == FW_UPDATE_START:
        return False # Start failed
    return False

    print("Started",result[0:10])

  def update_complete(self,firmware_version, restart=False):
    payload = Payload()
    payload.append(0x88) # Length
    payload.append(0x00)
    payload.append(0x00)
    payload.append(FW_UPDATE_COMPLETED)

    payload.append(0, 4) # Reserve 4 bytes for CRC

    payload.append(self.session_id, 4) # Session ID
    
    if restart:
      payload.append(FW_UPDATE_FN_RESTART)
    else:
      payload.append(FW_UPDATE_FN_PROG)

    payload.append(firmware_version)
    #payload.append(1,120)
   
    payload.append(0,3)

    crc = self.calculate_crc(payload[8:])
    payload.replace(4,crc,4)
 
    self.i2c_write(payload)
    time.sleep(0.04)

 
    if restart:
      return True
    else:
      # Wait for finish confirmation
      ts = int(round(time.time() * 1000))
      while int(round(time.time() * 1000)) < (ts + 10000):
        result = self.handle_exception()
        if result[4] == FW_UPDATE_COMPLETED and result[6] == 0:
          return True  # Finish success
        elif result[4] == FW_UPDATE_COMPLETED:
          print("Finish failed",result)
          return False # Finish failed
      return False



  
  def verify_block(self, block_addr, block_len, block_data):
    payload = Payload()
    payload.append(0x8c)
    payload.append(0x00) 
    payload.append(0x00)
    payload.append(FW_UPDATE_BLOCK)
    
    payload.append(0, 4)

    payload.append(block_addr,2) # Address to program
    payload.append(block_len, 1) # Length of block to program
    payload.append(FW_UPDATE_FN_VERIFY) # Set to program
    payload.append(block_data) # Actual payload

    crc = self.calculate_crc(payload[8:])
    payload.replace(4,crc,4)

    self.i2c_write(payload)
  
    time.sleep(0.04)
 
    # Wait for finish confirmation
    ts = int(round(time.time() * 1000))
    while int(round(time.time() * 1000)) < (ts + 10000):
      result = self.handle_exception()
      if result[4] == FW_UPDATE_BLOCK and result[6] == 0:
        print("BLOCK VERIFIED")
        return True
      elif result[4] == FW_UPDATE_BLOCK:
        print("BLOCK VERIFY FAILED",result[0:10])
        return False
    print("BLOCK VERIFY TIMEOUT")
    return False
      

  def update_block(self, block_addr, block_len, block_data):
    payload = Payload()
    payload.append(0x8c)
    payload.append(0x00) 
    payload.append(0x00)
    payload.append(FW_UPDATE_BLOCK)
    
    payload.append(0, 4)

    #block_data = block_data[0:block_len-1]

    #block_data.reverse()
    #for x in range(128-(block_len-1)):
    #  block_data.append(0)
    #block_data.reverse()

    payload.append(block_addr,2) # Address to program
    payload.append(block_len, 1) # Length of block to program
    payload.append(FW_UPDATE_FN_PROG) # Set to program
    payload.append(block_data) # Actual payload

    crc = self.calculate_crc(payload[8:])
    payload.replace(4,crc,4)

    self.i2c_write(payload)
    time.sleep(0.04) # Totally fails if no delay here, output buffer issue?
   
    payload = None
 
    # Wait for finish confirmation
    ts = int(round(time.time() * 1000))
    while int(round(time.time() * 1000)) < (ts + 20000):
      result = self.handle_exception()
      if result[4] == FW_UPDATE_BLOCK and result[6] == 0:
        print("BLOCK OK")
        return True
      elif result[4] == FW_UPDATE_BLOCK:
        print("BLOCK FAILED",result[0:10])
        return False
    print("BLOCK TIMEOUT")
    return False

  def handle_fw_info(self, timeout = 30000):
    ts = int(round(time.time() * 1000))
    while int(round(time.time() * 1000)) < (ts+timeout):
      fwversion = updater.handle_exception()
      if fwversion[3] == 0x83:
        print(fwversion[12:])
        return True
    return False
 
  def reset(self):
    GPIO.output(SW_RESET_PIN, GPIO.LOW)
    time.sleep(0.04)
    GPIO.output(SW_RESET_PIN, GPIO.HIGH)
    time.sleep(0.04)

  def handle_exception(self):
    '''
     1 - Unknown Command
     2 - Invalid Session ID
     3 - Invalid CRC
     4 - Invalid Length
     5 - Invalid Address
     6 - Invalid Function
     8 - Content Mixmatch
     12- Wrong Param
    '''
    while GPIO.input(SW_XFER_PIN):
      pass
    if not GPIO.input(SW_XFER_PIN):
      GPIO.setup(SW_XFER_PIN, GPIO.OUT, initial=GPIO.LOW)
      try:
        data = self.i2c.transaction(i2c.reading(SW_ADDR, 132))
        #print('RESPONSE:', data[0])
        return data[0]
      except IOError:
        return [0,0,0,0,0,0,0,0,0,0]
      GPIO.setup(SW_XFER_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)

class Payload(list):
  def __init__(self):
    list.__init__(self)

  def replace(self, start, value, size=1):
    if type(value) == int: # or type(value) == long:
      self[start:start+size] = value.to_bytes(length=size,byteorder="little",signed=False)
      #for x in range(size):
      #  self[start+x] = int(value >> (((size-1)*8) - 8*x) & 0xFF)
    elif type(value) == str:
      x = 0
      for char in value:
        self[start+x] = ord(char)
        x+=1
    elif type(value) == list:
      self[start, start+len(value)] = value

  def append(self, value, size=1):
    '''
    Append to the Payload
    value = String, list or integer
    size  = size of supplied integer in bytes
    '''
    if type(value) == int: # or type(value) == long:
      #print('Convert int',value,size)
      for x in value.to_bytes(length=size,byteorder="little",signed=False):
        list.append(self, x)
      #for x in range(size):
      #  list.append(self, int(value >> (((size-1)*8) - 8*x) & 0xFF ))      
    elif type(value) == str:
      for char in value:
        list.append(self, ord(char))    
    elif type(value) == list:
      for item in value:
        list.append(self,item)

updater = Skyware()
updater.reset()

#print(updater.handle_exception())
#while True:
#  time.sleep(1)
#  print(updater.handle_exception())
#exit()

#updater.handle_exception()

wait_for_fw_msg = True

if wait_for_fw_msg:
  proceed = False
  while proceed == False:
    print("Waiting for library loader version info...")
    ts = int(round(time.time() * 1000))
    print("Start time", ts)
    while int(round(time.time() * 1000)) < (ts+30000) and proceed == False:
      fwversion = updater.handle_exception()
      if fwversion[3] == 0x83:
        #print("Got firmware message:", fwversion)
        proceed = True

update_loader = True

if update_loader:

  #print("Starting loader update...")
  if updater.update_begin(fw.LDR_IV):
    print("Loader update started...")
  else:
    print("Loader update failed...")
    exit()
  #print(updater.handle_exception())

  idx = 0
  for page in fw.LDR_UPDATE_DATA:
    address = page[0]
    length  = page[1]
    print(str(idx) + ": Updating addr: ", address)
    updater.update_block(address, length, page[2:])
    #updater.verify_block(address, length, page[2:])
    idx+=1

  print("Finishing update...")
  updater.update_complete(fw.LDR_VERSION)
  time.sleep(0.5)
  updater.update_complete(fw.LDR_VERSION, True)

  print("Waiting 25sec for soft reset...")
  time.sleep(25)

  print("Issuing hard-reset...")
  updater.reset()

  print("Waiting for firmware info...")
  updater.handle_fw_info()


'''
  proceed = True

  while proceed == False:
    print("Waiting for library loader version info...")
    ts = int(round(time.time() * 1000))
    print("Start time", ts)
    while int(round(time.time() * 1000)) < (ts+30000) and proceed == False:
      fwversion = updater.handle_exception()
      if fwversion[3] == 0x83:
        proceed = True

if update_loader:
  time.sleep(0.5)

'''

'''
After power-on or hardware reset, the Library Loader
routine is executed.

FW_Version_Info message comes first, then a 100ms timeout
during which the library update should be initiated.

Once update is started, Library Loader should stay in
update mode until the next reset.
'''
#updater.handle_fw_info()

if updater.update_begin(fw.FW_IV):
  print("Started Library update...")
else:
  print("Failed to start library update..,")
  exit()

idx = 0
for page in fw.FW_UPDATE_DATA:
  address = page[0]
  length  = page[1]
  print(str(idx) + ": Updating addr: ", address)
  updater.update_block(address, length, page[2:])
  #print("Verifying...")
  #updater.verify_block(address, length, page[2:])
  #time.sleep(0.01)
  idx+=1

print("Finishing update...")
updater.update_complete(fw.FW_VERSION)

#proceed = False

#while proceed == False:
print("Issuing reset...")
updater.update_complete(fw.FW_VERSION, True)

time.sleep(25)

updater.reset()
updater.handle_fw_info()
#time.sleep(0.2)

#print("Waiting for library loader version info...")
#proceed = updater.handle_fw_info(30000) 

print("Verifying update...")
if updater.update_begin(fw.FW_IV,True):

  for page in fw.FW_UPDATE_DATA:
    address = page[0]
    length  = page[1]
    print("Verifying addr: ", address)
    updater.verify_block(address, length, page[2:])
else:
  print("Failed starting verify...")

print("Resetting...")
updater.reset()
updater.handle_fw_info()
while True:
  print(updater.handle_exception())
