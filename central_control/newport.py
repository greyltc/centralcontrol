from pyftdi.gpio import GpioController
import time

class Newport:
    direction = 0xff # gpio direction byte
    def __init__(self, address = 'ftdi://ftdi:232/1'):
        self.gpio = GpioController()
        self.state = None
        self.address = address
        
    def __del__(self):
        self.disconnect()
        
    def connect(self):
        self.gpio.open_from_url(self.address,direction=self.direction)
        self.state = self.gpio.read()
        
    def disconnect(self):
        self.gpio.close()
        
    def on(self):
        self.gpio.write(0x00)
        self.state = self.gpio.read()
        
    def off(self):
        self.gpio.write(0xff)
        self.state = self.gpio.read()

    def get_spectrum(self):
        x = []
        y = []
        return (x,y)

if __name__ == "__main__":
    from os import environ
    address = environ.get('FTDI_DEVICE', 'ftdi://ftdi:232/1')    
    np = Newport(address=address)
    np.connect()
    np.on()
    time.sleep(1)
    np.off()
    np.disconnect()
