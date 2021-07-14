#!/usr/bin/env python3

# this is a server program which listens for connections from the WaveLabs solar sim software on port
# wl_port (defaults to 3334) and for connections from solar sim control clients on port coltrol_port (defaults to 3335)
# communication messages are relayed to/from the control clients and the WaveLabs software
import socketserver

# global selector
sel = socketserver.selectors.DefaultSelector()


def accept(sock):
  conn, addr = sock.accept()  # accept the initial connection
  print('Accepted new connection: {:} from ip {:}'.format(conn, addr))
  conn.setblocking(False)
  sel.register(conn, socketserver.selectors.EVENT_READ, get_data)
  return conn


def get_data(conn):
  data = conn.recv(1024)  # Should be ready
  if data:
    pass
  else:
    print('closing', conn)
    sel.unregister(conn)
    conn.close()
  return (conn, data)


def setupServer(listen_ip, listen_port):
  server = socketserver.TCPServer((listen_ip, listen_port), socketserver.StreamRequestHandler, bind_and_activate=False)
  server.timeout = None  # never timeout when waiting for the wavelabs software to connect
  server.allow_reuse_address = True
  server.server_bind()
  server.server_activate()
  return server


def main():
  wl_port = 3334  # port for connections from the WaveLabs software
  control_port = 3335  # port for connections from control client software
  listen_ip = "0.0.0.0"  # ip address these servers will listen on

  print("Launching relay server")

  # setup a server for connections from WaveLabs
  wl_server = setupServer(listen_ip, wl_port)
  sel.register(wl_server.socket, socketserver.selectors.EVENT_READ, accept)

  # setup a server for connections from control client software
  control_server = setupServer(listen_ip, control_port)
  sel.register(control_server.socket, socketserver.selectors.EVENT_READ, accept)

  wl_conn = None
  control_conn = None
  while True:
    events = sel.select()
    for key, mask in events:
      callback = key.data
      callback_return = callback(key.fileobj)
      if type(callback_return) == socketserver.socket.socket:
        # this was a new connection
        conn = callback_return
        port = conn.getsockname()[1]
        if port == wl_port:
          wl_conn = conn
        elif port == control_port:
          control_conn = conn
      else:
        # this was not a new connection (either disconnect or new data to echo)
        conn, data = callback_return
        if not conn._closed:
          port = conn.getsockname()[1]
          if port == wl_port and data:
            # new WaveLabs data to echo to control client
            try:
              control_conn.sendall(data)
            except:
              print('WARNING: Unable to relay WaveLabs client data to control client')
          if port == control_port and data:
            # new control data to echo to WaveLabs client
            try:
              wl_conn.sendall(data)
            except:
              print('WARNING: Unable to relay control client data to WaveLabs client')


if __name__ == "__main__":
  main()
