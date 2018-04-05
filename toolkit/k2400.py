import visa # for talking to sourcemeter
import pyvisa
import serial
import sys

class k2400:
  """
  Intertace for Keithley 2400 sourcemeter
  """  
  
  def __init__(self, visa_lib = '@py'):
    try:
      self.rm = visa.ResourceManager(visa_lib)
    except:
      exctype, value1 = sys.exc_info()[:2]
      try:
        self.rm = visa.ResourceManager()
      except:
        exctype, value2 = sys.exc_info()[:2]
        myPrint('Unable to connect to instrument.', file=sys.stderr, flush=True)
        myPrint('Error 1 (using {:s} backend):'.format(visa_lib), file=sys.stderr, flush=True)
        myPrint(value1, file=sys.stderr, flush=True)
        myPrint('Error 2 (using pyvisa default backend):', file=sys.stderr, flush=True)
        myPrint(value2, file=sys.stderr, flush=True)
    vLibPath = self.rm.visalib.get_library_paths()[0]
    if vLibPath == 'unset':
      backend = 'pyvisa-py'
    else:
      backend = vLibPath
    print("Using {:s} pyvisa backend.".format(backend))