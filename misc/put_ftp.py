#!/usr/bin/env python

import ftplib
import argparse
import os
import socket
import ipaddress
import sys

parser = argparse.ArgumentParser(description='Upload files to a passwordless FTP server')
parser.add_argument('-v', '--verbose', action='store_true', default=False, help="Be verbose")
parser.add_argument('-s', '--server', default='epozz', help="FTP server hostname or IP address")
parser.add_argument('-r', '--remote_path', default='/drop/', help="Remote path to upload into (needs trailing slash)")
parser.add_argument('files', type=argparse.FileType('rb'), nargs='+', help="File(s) to upload")

args = parser.parse_args()

if args.files == []:
  if args.verbose:
    print("Nothing to upload")
  sys.exit(-1)
else:
  # sanitize server input
  server_ip_string = socket.gethostbyaddr(args.server)[2][0]
  ip = ipaddress.ip_address(server_ip_string)
  
  ftp = ftplib.FTP(ip.exploded)
  ftp.login()
  
  for f in args.files:
    file_name = os.path.basename(f.name)
    if args.verbose:
      print('Uploading {:}...'.format(f.name))
    ftp.storbinary('STOR {:}{:}'.format(args.remote_path, file_name), f)
    if args.verbose:
      print('Success: uploaded to {:}:{:}{:}'.format(args.server, args.remote_path, file_name))
    
  ftp.quit()