from toolkit import k2400
from toolkit import pcb
from toolkit import virt

class logic:
  
  def __init__(self, dummy=False, visa_lib='@py', scan=False, addressString=None, terminator='\n', serialBaud=57600, front=False, twoWire=False, pixel_address='A1'):
    if dummy:
      self.sm = k2400_virt()
      self.pcb = pcb_virt()
      self.pixel_address = pixel_address
    else:
      sm = k2400(visa_lib=visa_lib, terminator=terminator, addressString=args.address, serialBaud=args.baud, scan=args.scan)
    
    
  
  
  def find_ss_voc(self, sm, pcb):
    pass