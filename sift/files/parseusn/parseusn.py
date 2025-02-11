#!/usr/bin/env python
'''
------------------------------
parseusn.py

Dave Lassalle, @superponible
email: dave@superponible.com
------------------------------

This is an adaptation of UsnJrnl.py at by https://code.google.com/p/parser-usnjrnl/,
which is based on USNJRNL parser blog from Lance Mueller
(http://www.forensickb.com/2008/09/enscript-to-parse-usnjrnl.html)

This script will parse the entries from the $USNJRNL$J alternate data stream used by NTFS filesystem.
To use the script, extract the journal using a forensic tool such as EnCase, FTK, or ProDiscover.

This is intended to be cross platform and not memory intensive.

LICENSE: MIT Open Source License (http://opensource.org/licenses/mit-license.php)
'''

import struct
import binascii
import datetime
import sys
import os
import argparse

# GLOBAL variables
RECORD_HEADER = ('Timestamp', 'MFT Reference', 'MFT Sequence', 'Parent MFT Reference', 'Parent MFT Sequence', 'USN', 'Filename', 'Attributes', 'Change Type', 'Source Info')

FLAGS_LONG = {0x00: " ",
              0x01: "The data in the file or directory is overwritten.",
              0x02: "The file or directory was added to.",
              0x04: "The file or directory was truncated.",
              0x10: "Data in one or more named data streams for the file was overwritten.",
              0x20: "One or more named data streams for the file were added to.",
              0x40: "One or more named data streams for the file was truncated.",
              0x100: "The file or directory was created for the first time.",
              0x200: "The file or directory was deleted.",
              0x400: "The user made a change to the file's or directory's extended attributes.",
              0x800: "A change was made in the access rights to the file or directory.",
              0x1000: "The file or directory was renamed and the file name in this structure is the previous name.",
              0x2000: "The file or directory was renamed and the file name in this structure is the new name.",
              0x4000: "A user toggled the FILE_ATTRIBUTE_NOT_CONTENT_INDEXED attribute.",
              0x8000: "A user has either changed one or more file or directory attributes or one or more time stamps.",
              0x10000: "An NTFS hard link was added to or removed from the file or directory",
              0x20000: "The compression state of the file or directory was changed from or to compressed.",
              0x40000: "The file or directory was encrypted or decrypted.",
              0x80000: "The object identifier of the file or directory was changed.",
              0x100000: "The reparse point contained in the file or directory was changed, or a reparse point was added to or deleted from the file or directory.",
              0x200000: "A named stream has been added to or removed from the file or a named stream has been renamed.",
              0x80000000: "The file or directory was closed.",
              }

FLAGS_SHORT = {0x00: " ",
               0x01: "data_overwritten",
               0x02: "data_appended",
               0x04: "data_truncated",
               0x10: "ads_data_overwritten",
               0x20: "ads_data_appended",
               0x40: "ads_data_truncated",
               0x100: "file_created",
               0x200: "file_deleted",
               0x400: "extended_attrib_chnaged",
               0x800: "access_changed",
               0x1000: "file_old_name",
               0x2000: "file_new_name",
               0x4000: "context_indexed_changed",
               0x8000: "basic_info_changed",
               0x10000: "hardlink_changed",
               0x20000: "compression_changed",
               0x40000: "encryption_changed",
               0x80000: "objid_changed",
               0x100000: "reparse_changed",
               0x200000: "ads_added_or_deleted",
               0x80000000: "file_closed",
               }

# REM this is taken from http://msdn.microsoft.com/en-us/library/ee332330(VS.85).aspx
FILE_ATTRIBUTES = {32: 'ARCHIVE',
                   2048: 'COMPRESSED',
                   64: 'DEVICE',
                   16: 'DIRECTORY',
                   16384: 'ENCRYPTED',
                   2: 'HIDDEN',
                   128: 'NORMAL',
                   8192: 'NOT_CONTENT_INDEXED',
                   4096: 'OFFLINE',
                   1: 'READONLY',
                   1024: 'REPARSE_POINT',
                   512: 'SPARSE_FILE',
                   4: 'SYSTEM',
                   256: 'TEMPORARY',
                   65536: 'VIRTUAL',
                   }

SOURCEINFO = {4: "The operation is modifying a file to match the contents of the same file which exists in another member of the replica set.",
              2: "The operation adds a private data stream to a file or directory.",
              1: "The operation provides information about a cahnge to the file or directory made by the operating system.",
              0: "",
              }


def main(argv):
    args = cliargs()
    infile = args.infilename
    outfile = args.outfilename
    appendmode = args.appendmode
    mftfilename = args.mftfilename
    all_records = args.all_records
    flags = FLAGS_SHORT
    if args.long_flags is True:
        flags = FLAGS_LONG

    mft_to_path = {}
    if mftfilename:
        mft_to_path = parse_mft(mftfilename)

    create_temp_file(infile)

    it = file("{}.tmp".format(infile), 'rb')
    if outfile is None:
        ot = sys.stdout
    else:
        if appendmode is True:
            ot = file(outfile, 'ab')
        else:
            ot = file(outfile, 'wb')

    if args.out_format == 'csv':
        joinchar = '","'
    elif args.out_format == 'tab':
        joinchar = "\t"

    if args.out_format == 'csv' or args.out_format == 'tab':
        if args.out_format == "csv":
            ot.write('"')
        ot.write(joinchar.join(RECORD_HEADER))
        if args.out_format == "csv":
            ot.write('"')
        ot.write('\n')

    if args.out_format == 'body' or args.out_format == 'tln' or args.out_format == 'l2ttln':
        all_records = True
        joinchar = '|'

    read_length = 800
    position_marker = 0
    go = True

    while (go is True):
        try:
            # Read the record size, read the next record
            # sys.stderr.write("\b\b\b\b\b\b\b\b\b\b\b\b\b\b\b\b\b\b\b Offset {}".format(position_marker))

            it.seek(position_marker, os.SEEK_SET)
            data = it.read(read_length)
            if len(data) < 60:
                go = False
                continue

            recordsize = struct.unpack_from('I', data)[0]

            if (recordsize < 0):
                go = False          # Invalid data can create an endless loop
            if (recordsize < 60):
                # Note: There are places in the test $USNJRNL$J file where there are gaps between records that are not accounted for by the record size.
                # The gaps are always 0x00 filled. If the record size is zero, move forward until the next non zero byte is found. The largest gap I found was 296 bytes.

                gap_size = len(data.lstrip('\x00'))
                if gap_size < 1:
                    break
                if gap_size == read_length:
                    position_marker += 8
                    continue
                else:
                    if read_length - gap_size < 8:
                        position_marker += 8
                    else:
                        position_marker = position_marker + 800 - gap_size
                        # records are aligned at 0x0 or 0x8, so zero out least significant 3 bits
                        # this is necessary if the first non-zero byte is not found at an 0x0 or 0x8 offset
                        position_marker = position_marker & 0xfffffff8
                        continue

            it.seek(position_marker)
            data = it.read(recordsize)
            try:
                usn_record = decode_USN_record(data, recordsize)
            except struct.error:
                sys.stderr.write("\nCannot parse {} at offset {}\n".format(data, position_marker))
                sys.stderr.write("\nLength of data is {}\n".format(len(data)))
                position_marker = position_marker + recordsize      # Initially forgot this. A struct error would loop forever...
                continue
            if usn_record is None:
                position_marker = position_marker + recordsize
                continue
            usn_record = deflag_item(usn_record, flags)

            if (all_records or "closed" in usn_record['reason'] or "old_name" in usn_record['reason']):
                # Print in appropriate format
                filename = usn_record['filename']
                if mftfilename:
                    try:
                        filename = mft_to_path[usn_record['parent_ref']] + "\\" + filename
                    except KeyError:
                        filename = "[ORPHAN]\\" + filename
                if args.out_format == 'csv' or args.out_format == 'tab':
                    fields = (usn_record['time'],
                              usn_record['mft_ref'],
                              usn_record['mft_ref_seq'],
                              usn_record['parent_ref'],
                              usn_record['parent_ref_seq'],
                              usn_record['usn'],
                              filename,
                              usn_record['file_attrib'],
                              usn_record['reason'],
                              usn_record['sourceinfo'],
                              )
                elif args.out_format == 'tln':
                    fields = (int((usn_record['time'] - datetime.datetime(1970, 1, 1)).total_seconds()),
                              'USN',
                              '-',
                              '-',
                              str(usn_record['mft_ref']) + ';' + filename + ';' + usn_record['reason']
                              )
                elif args.out_format == 'l2ttln':
                    fields = (int((usn_record['time'] - datetime.datetime(1970, 1, 1)).total_seconds()),
                              'USN',
                              '-',
                              '-',
                              str(usn_record['mft_ref']) + ';' + filename + ';' + usn_record['reason'],
                              'UTC',
                              '-'
                              )
                else:
                    # print body file format
                    usn_epoch_time = int((usn_record['time'] - datetime.datetime(1970, 1, 1)).total_seconds())
                    atime = usn_epoch_time
                    mtime = usn_epoch_time
                    ctime = usn_epoch_time
                    etime = usn_epoch_time
                    fields = ('0',
                              filename,
                              usn_record['mft_ref'],
                              '',
                              '0',
                              '0',
                              '0',
                              atime,
                              mtime,
                              ctime,
                              etime,
                              )
                try:
                    if args.out_format == "csv":
                        ot.write('"')
                    ot.write(joinchar.join(["{}".format(a) for a in fields]))
                    if args.out_format == "csv":
                        ot.write('"')
                    ot.write('\n')
                    ot.flush()
                except IOError:
                    try:
                        sys.stdout.close()
                    except IOError:
                        pass
                    try:
                        sys.stderr.close()
                    except IOError:
                        pass

            usn_record = None
            position_marker = position_marker + recordsize

        except struct.error as e:
            sys.stderr.write(e.message)
            go = False
            sys.stderr.write("Struct format error at Tell: {}\n".format(it.tell()))

        except:
            go = False
            print(("Unexpected error:", sys.exc_info()[0]))
            raise

    it.close()
    ot.close()

    # replace original sparse file USN with temp to save space since beginning of file is just NULLs
    if args.replace:
      os.unlink("{}".format(infile))
      os.rename("{}.tmp".format(infile), "{}".format(infile))
    else:
      os.unlink("{}.tmp".format(infile))

    exit(0)


def cliargs():
    '''Parse CLI args'''
    parser = argparse.ArgumentParser(description="parseusn.py -- USN Journal Parser")
    parser.add_argument('-f', '--infile', required=True, action='store', dest='infilename', help='Input filename, extracted $UsnJrnl:$J')
    parser.add_argument('-m', '--mft', required=False, action='store', dest='mftfilename', help='MFT filename, for getting the full path')
    parser.add_argument('-o', '--outfile', required=False, action='store', dest='outfilename', help='Output filename, default to STDOUT')
    parser.add_argument('-t', '--type', required=False, action='store', dest='out_format', default="csv", choices=['csv', 'tab', 'body', 'tln', 'l2ttln'],
                        help='Output format, default to CSV'
                        )
    parser.add_argument('-A', '--append', required=False, action='store_true', dest='appendmode', default=False,
                        help='Open output file in append mode instead of overwrite.  Only applies when output file is specified.'
                        )
    parser.add_argument('-a', '--all', required=False, action='store_true', dest='all_records', default=False,
                        help='Print all records, not just closed records. True for body and tln output.'
                        )
    parser.add_argument('-l', '--long', required=False, action='store_true', dest='long_flags', default=False,
                        help='Print long strings for the file attribute flgas.'
                        )
    parser.add_argument('-r', '--replace', required=False, action='store_true', dest='replace', default=False,
                        help='Replace original file with temp file (removes NULLs in sparse file).'
                        )
    args = parser.parse_args()
    return args


def create_temp_file(infile):
    '''$USNJRNL files can contain a large amount of leading zeros.
    Create a smaller file that eliminate them.'''
    it = file(infile, 'rb')
    while (True):
        data = it.read(6553600)
        data = data.lstrip('\x00')
        if len(data) > 0:
            break
    position = it.tell() - len(data)
    it.seek(position)

    # replace main file with working file, then clean up
    ot = file("{}.tmp".format(infile), 'wb')
    while (True):
        data = it.read(655360)
        if len(data) < 655359:
            ot.write(data)
            break
        else:
            ot.write(data)

    it.close()
    ot.close()
    data = ''


def parse_mft(mftfile):
    '''Get a mapping for MFTNUMBER:PATH to display full path in output'''
    mft_to_name = {}
    mft_to_parent = {}
    mft_to_path = {}
    # open the MFT and read one a 1K record at a time until the end
    it = file(mftfile, 'rb')
    while(True):
        data = it.read(1024)
        if len(data) <= 0:
            break
        if data[0:5] != 'FILE0':
            continue
        mftnum = struct.unpack("i", data[44:48])[0]
        # find the start of the first attribute field then get its type
        attrib_start = struct.unpack("h", data[20:22])[0]
        attrib_type = struct.unpack("i", data[attrib_start:attrib_start + 4])[0]
        name = ""
        parent = -1
        # loop until attribute type is 0xffffffff
        while attrib_type != -1:
            # only care about filename attributes (0x30)
            if attrib_type == 48:
                # step through filename attribute fields and get parent MFT number, filename length, and filename
                stream_length = 2 * struct.unpack("B", data[attrib_start + 9])[0]
                parent_start = attrib_start + 24 + int(stream_length)
                parent = struct.unpack("i", data[parent_start:parent_start + 4])[0]
                name_length_start = parent_start + 64
                name_length = int(struct.unpack("b", data[name_length_start])[0])
                name_start = name_length_start + 2
                # unicodeHack for MFT names taken from analyzeMFT.py
                name = ''
                for i in range(name_start, name_start + name_length * 2):
                    if data[i] != '\x00':                         # Just skip over nulls
                        if data[i] > '\x1F' and data[i] < '\x80':          # If it is printable, add it to the string
                            name = name + data[i]
                        else:
                            name = "%s0x%02s" % (name, data[i].encode("hex"))
            # go to the next attribute and get its type, then loop
            attrib_length = struct.unpack("h", data[attrib_start + 4:attrib_start + 6])[0]
            attrib_start += attrib_length
            attrib_type = struct.unpack("i", data[attrib_start:attrib_start + 4])[0]
        # finished looping through all the FN attributes, so set the name and parent
        # TODO: right now this is only going to use the last FN attribute.  Usually that probably works and
        # will get the long filename attribute.  I have seen MFT records with 3 FN attributes; not sure how
        # those work right now.
        mft_to_name[mftnum] = name
        mft_to_parent[mftnum] = parent
    # loop through all the MFT numbers and build a dictionary mapping MFT number to full file path
    for mftnum in list(mft_to_parent.keys()):
        path = ""
        num = mftnum
        # use this so we don't repeatedly build the same path
        first_parent = True
        # start with the name of the file with the given MFT number
        path = mft_to_name[mftnum]
        while num != 5 and num != -1:
            # get the MFT number of the parent dir
            num = mft_to_parent[num]
            # if parent is 5, it's in the root directory
            if num == 5:
                path = ".\\" + path
                break
            # if parent is -1, it has no parent
            if num == -1:
                path = "NoParent\\" + path
                break
            # if the parent has already been found, use that value instead of rebuilding it
            if mft_to_path.get(num, -2) != -2 and first_parent:
                path = mft_to_path[num] + "\\" + path
                break
            # first parent was not found, so we don't do that check anymore
            first_parent = False
            # add the parent to the beginning of the path, then loop
            try:
                path = mft_to_name[num] + "\\" + path
            except KeyError:
                path = "[ORPHAN]\\" + path
                break
        # mft number of 5 is the root directory, for everything else, set the path
        if mftnum == 5:
            mft_to_path[mftnum] = '.'
        else:
            mft_to_path[mftnum] = path
    return mft_to_path


def deflag_item(r, flags):
    '''Replaces values where needed for each tuple, returns new
    If flags do not exits, then return same value'''

    filename = r['filename']
    # drop anything after the first double-null, some lines have garbage at the end
    filename = filename[:filename.find('\x00\x00')]
    # strip the extra hex zeros put in by MS encoding
    r['filename'] = filename.replace('\x00', '')

    # convert 64-bit windows time to human readable date
    r['time'] = conv_time(r['time'])

    try:
        r['reason'] = flags[r['reason']]
    except KeyError:
        r['reason'] = deflag_long_field(r['reason'], flags)
    try:
        r['sourceinfo'] = SOURCEINFO[r['sourceinfo']]
    except KeyError:
        r['sourceinfo'] = deflag_long_field(r['sourceinfo'], SOURCEINFO)
    try:
        r['file_attrib'] = FILE_ATTRIBUTES[r['file_attrib']]
    except KeyError:
        r['file_attrib'] = deflag_long_field(r['file_attrib'], FILE_ATTRIBUTES)
    return r


def deflag_long_field(value, flags):
    '''In the event that more than one flag is set for a field,
    this will read through the flags and concatenate the values.'''
    setflags = []

    keylist = sorted(flags.keys())
    for i in keylist:
        if i & value > 0:
            setflags.append(flags[i])
    return "; ".join(setflags)


def decode_USN_record(d, size):
    '''Given a chunk of data and its size, parse out the fields of the USN header'''
    r = {}
    r['length'] = size

    # Combine Major Minor version fields
    r['major'] = struct.unpack("h", d[4:6])[0]
    r['minor'] = struct.unpack("h", d[6:8])[0]
    r['version'] = "{}.{}".format(r['major'], r['minor'])

    if r['major'] == 2:
        r['mft_ref'] = struct.unpack("ixx", d[8:14])[0]
        r['mft_ref_seq'] = struct.unpack("h", d[14:16])[0]
        r['parent_ref'] = struct.unpack("ixx", d[16:22])[0]
        r['parent_ref_seq'] = struct.unpack("h", d[22:24])[0]
        r['usn'] = struct.unpack("q", d[24:32])[0]
        r['time'] = binascii.hexlify(struct.unpack("8s", d[39:31:-1])[0])
        r['reason'] = struct.unpack("i", d[40:44])[0]
        r['sourceinfo'] = struct.unpack("i", d[44:48])[0]
        r['securityid'] = struct.unpack("i", d[48:52])[0]
        r['file_attrib'] = struct.unpack("i", d[52:56])[0]
        r['filename_length'] = struct.unpack("h", d[56:58])[0]
        r['filename_offset'] = struct.unpack("h", d[58:60])[0]
        off = r['filename_offset']
        length = r['filename_length']
        r['filename'] = struct.unpack("{}s".format(length), d[off:off + length])[0]
    # TODO: this needs to be tested on a system with actual 3.0 records
    elif r['major'] == 3:
        mft_ref1, mft_ref2 = struct.unpack("<QQ", d[8:24])[0]
        r['mft_ref'] = (mft_ref2 << 64) | mft_ref1
        r['mft_ref_seq'] = 0
        # assert u.int == r['mft_ref']
        parent_ref1, parent_ref2 = struct.unpack("<QQ", d[24:40])[0]
        r['parent_ref'] = (parent_ref2 << 64) | parent_ref1
        r['parent_ref_seq'] = 0
        # assert u.int == r['parent_ref']
        r['usn'] = struct.unpack("q", d[40:48])[0]
        r['time'] = binascii.hexlify(struct.unpack("8s", d[55:47:-1])[0])
        r['reason'] = struct.unpack("i", d[56:60])[0]
        r['sourceinfo'] = struct.unpack("i", d[60:64])[0]
        r['securityid'] = struct.unpack("i", d[64:68])[0]
        r['file_attrib'] = struct.unpack("i", d[68:72])[0]
        r['filename_length'] = struct.unpack("h", d[72:74])[0]
        r['filename_offset'] = struct.unpack("h", d[74:76])[0]
        off = r['filename_offset']
        length = r['filename_length']
        r['filename'] = struct.unpack("{}s".format(length), d[off:off + length])[0]
    else:
        r = None
    return r


def conv_time(dt):
    '''convert Windows 64-bit time, passed as big endian string representation, to datetime value'''
    us = int(dt, 16) / 10.
    return datetime.datetime(1601, 1, 1) + datetime.timedelta(microseconds=us)

if __name__ == '__main__':
    main(sys.argv[1:])
