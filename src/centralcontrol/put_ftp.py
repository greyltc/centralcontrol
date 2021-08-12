#!/usr/bin/env python

import ftplib
import argparse
import os
import socket
import ipaddress
import sys


class put_ftp:
    verbose = False
    remote_path = None

    # need __enter__ and exit for use with "with"
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def __init__(self, address, pasv=True):

        # sanitize address input
        protocol, address = address.split("://")
        host_path_split = address.split("/", 1)
        host = host_path_split[0]
        if len(host_path_split) == 2:
            self.remote_path = "/" + host_path_split[1]
        else:
            self.remote_path = "/"
        host_split = host.split(":")
        host = host_split[0]
        port = 21
        if len(host_split) == 1:
            if protocol == "ftp":
                port = 21
            else:  # possibility to handle default ports for other protocols here
                port = 21
        else:
            port = int(host_split[1])

        try:
            ip = ipaddress.ip_address(host)
        except:
            server_ip_string = socket.gethostbyname_ex(host)[2][0]
            ip = ipaddress.ip_address(server_ip_string)

        self.ftp = ftplib.FTP()
        self.ftp.connect(host=ip.exploded, port=port)
        if pasv == False:
            self.ftp.passiveserver = 0
        else:
            self.ftp.passiveserver = 1
        self.ftp.login()

    def uploadFile(self, file_pointer, remote_path=None):
        if remote_path == None:
            remote_path = self.remote_path
        file_name = os.path.basename(file_pointer.name)
        if self.verbose:
            print("Uploading {:}...".format(file_pointer.name))
        # so maybe we need to create an arbitrary number of nested remote directories
        first_part, second_part = os.path.split(remote_path)
        path_list = []
        while first_part != "/":
            path_list.append(first_part)
            first_part, second_part = os.path.split(first_part)
        path_list.reverse()
        for directory in path_list:  # actually create the directories now
            try:
                self.ftp.mkd(directory)
            except ftplib.error_perm:
                pass  # directory probably already exists
        self.ftp.storbinary("STOR {:}{:}".format(remote_path, file_name), file_pointer)  # upload the file
        if self.verbose:
            print("Success: uploaded to {:}:{:}{:}{:}".format(self.ftp.host, self.ftp.port, remote_path, file_name))

    def close(self):
        self.ftp.quit()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Upload files to a passwordless FTP server")
    parser.add_argument("address", type=str, help='complete ftp server address and remote path to upload to, eg "ftp://epozz:21/drop/"')
    parser.add_argument("-a", "--active", action="store_true", default=False, help="Use active transfer mode instead of passive")
    parser.add_argument("-v", "--verbose", action="store_true", default=False, help="Be verbose")
    parser.add_argument("files", type=argparse.FileType("rb"), nargs="+", help="File(s) to upload")

    args = parser.parse_args()

    if args.files == []:
        if args.verbose:
            print("Nothing to upload")
        sys.exit(-1)

    with put_ftp(args.address, pasv=not args.active) as ftp:
        ftp.verbose = args.verbose
        for f in args.files:
            ftp.uploadFile(f)
