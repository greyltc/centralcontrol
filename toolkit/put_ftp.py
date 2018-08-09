#!/usr/bin/env python

import ftplib
import argparse
import os
import socket
import ipaddress
import sys

class put_ftp:
  verbose = False
  def __init__(self,server):
    
    # sanitize server input
    server_ip_string = socket.gethostbyaddr(server)[2][0]
    ip = ipaddress.ip_address(server_ip_string)
    
    self.ftp = ftplib.FTP(ip.exploded)
    self.ftp.login()

  def uploadFile(self, file_pointer, remote_path):
    file_name = os.path.basename(file_pointer.name)
    if self.verbose:
      print('Uploading {:}...'.format(file_pointer.name))
    # so maybe we need to create an arbitrary number of nested remote directories
    first_part, second_part = os.path.split(remote_path)
    path_list = []
    while first_part != '/':
      path_list.append(first_part)
      first_part, second_part = os.path.split(first_part)
    path_list.reverse()
    for directory in path_list: # actually create the directories now
      try:
        self.ftp.mkd(directory)
      except ftplib.error_perm:
        pass # directory probably already exists
    self.ftp.storbinary('STOR {:}{:}'.format(remote_path, file_name), file_pointer) #upload the file
    if self.verbose:
      print('Success: uploaded to {:}:{:}{:}'.format(args.server, remote_path, file_name))    

  def close(self):
    self.ftp.quit()

if __name__ == "__main__":
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
    ftp = put_ftp(args.server)
    ftp.verbose = args.verbose

    for f in args.files:
      ftp.uploadFile(f, args.remote_path)

    ftp.close()