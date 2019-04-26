from xmlrpc.server import SimpleXMLRPCServer
from xmlrpc.server import SimpleXMLRPCRequestHandler

# Restrict to a particular path.
class RequestHandler(SimpleXMLRPCRequestHandler):
  rpc_paths = ('/RPC2',)

class server:
  """
  handles xmlrpc comms to/from the control software
  """
  rpc_server = None
  
  def __init__(self, interface, port):
    self.rpc_server = SimpleXMLRPCServer((interface, port), requestHandler=RequestHandler)
    self.rpc_server.register_introspection_functions()
    self.rpc_server.serve_forever
  
  def __del__(self):
    try:
      self.rpc_server.shutdown()
    except:
      pass
    
  def run_server(self):
    self.rpc_server.serve_forever()
    
  def stop_server(self):
    self.rpc_server.shutdown()