# Common functions used throughout various parts of binwalk code.

import io
import os
import re
import sys
import ast
import hashlib
import operator as op
from binwalk.core.compat import *

# This allows other modules/scripts to subclass BlockFile from a custom class. Defaults to io.FileIO.
if has_key(__builtins__, 'BLOCK_FILE_PARENT_CLASS'):
    BLOCK_FILE_PARENT_CLASS = __builtins__['BLOCK_FILE_PARENT_CLASS']
else:
    BLOCK_FILE_PARENT_CLASS = io.FileIO

# The __debug__ value is a bit backwards; by default it is set to True, but
# then set to False if the Python interpreter is run with the -O option.
if not __debug__:
    DEBUG = True
else:
    DEBUG = False

def debug(msg):
    '''
    Displays debug messages to stderr only if the Python interpreter was invoked with the -O flag.
    '''
    if DEBUG:
        sys.stderr.write("DEBUG: " + msg + "\n")
        sys.stderr.flush()

def warning(msg):
    '''
    Prints warning messages to stderr
    '''
    sys.stderr.write("\nWARNING: " + msg + "\n")

def error(msg):
    '''
    Prints error messages to stderr
    '''
    sys.stderr.write("\nERROR: " + msg + "\n")

def get_module_path():
    root = __file__
    if os.path.islink(root):
        root = os.path.realpath(root)
    return os.path.dirname(os.path.dirname(os.path.abspath(root)))

def get_libs_path():
    return os.path.join(get_module_path(), "libs")

def file_md5(file_name):
    '''
    Generate an MD5 hash of the specified file.
    
    @file_name - The file to hash.

    Returns an MD5 hex digest string.
    '''
    md5 = hashlib.md5()

    with open(file_name, 'rb') as f:
        for chunk in iter(lambda: f.read(128*md5.block_size), b''):
            md5.update(chunk)

    return md5.hexdigest()

def file_size(filename):
    '''
    Obtains the size of a given file.

    @filename - Path to the file.

    Returns the size of the file.
    '''
    # Using open/lseek works on both regular files and block devices
    fd = os.open(filename, os.O_RDONLY)
    try:
        return os.lseek(fd, 0, os.SEEK_END)
    except KeyboardInterrupt as e:
        raise e
    except Exception as e:
        raise Exception("file_size failed to obtain the size of '%s': %s" % (filename, str(e)))
    finally:
        os.close(fd)

def strip_quoted_strings(string):
    '''
    Strips out data in between double quotes.
    
    @string - String to strip.

    Returns a sanitized string.
    '''
    # This regex removes all quoted data from string.
    # Note that this removes everything in between the first and last double quote.
    # This is intentional, as printed (and quoted) strings from a target file may contain 
    # double quotes, and this function should ignore those. However, it also means that any 
    # data between two quoted strings (ex: '"quote 1" you won't see me "quote 2"') will also be stripped.
    return re.sub(r'\"(.*)\"', "", string)

def get_quoted_strings(string):
    '''
    Returns a string comprised of all data in between double quotes.

    @string - String to get quoted data from.

    Returns a string of quoted data on success.
    Returns a blank string if no quoted data is present.
    '''
    try:
        # This regex grabs all quoted data from string.
        # Note that this gets everything in between the first and last double quote.
        # This is intentional, as printed (and quoted) strings from a target file may contain 
        # double quotes, and this function should ignore those. However, it also means that any 
        # data between two quoted strings (ex: '"quote 1" non-quoted data "quote 2"') will also be included.
        return re.findall(r'\"(.*)\"', string)[0]
    except KeyboardInterrupt as e:
        raise e
    except Exception:
        return ''

def unique_file_name(base_name, extension=''):
    '''
    Creates a unique file name based on the specified base name.

    @base_name - The base name to use for the unique file name.
    @extension - The file extension to use for the unique file name.

    Returns a unique file string.
    '''
    idcount = 0
    
    if extension and not extension.startswith('.'):
        extension = '.%s' % extension

    fname = base_name + extension

    while os.path.exists(fname):
        fname = "%s-%d%s" % (base_name, idcount, extension)
        idcount += 1

    return fname

def strings(filename, minimum=4):
    '''
    A strings generator, similar to the Unix strings utility.

    @filename - The file to search for strings in.
    @minimum  - The minimum string length to search for.

    Yeilds printable ASCII strings from filename.
    '''
    result = ""

    with BlockFile(filename) as f:
        while True:
            (data, dlen) = f.read_block()
            if not data:
                break

            for c in data:
                if c in string.printable:
                    result += c
                    continue
                elif len(result) >= minimum:
                    yield result
                    result = ""
                else:
                    result = ""

class MathExpression(object):
    '''
    Class for safely evaluating mathematical expressions from a string.
    Stolen from: http://stackoverflow.com/questions/2371436/evaluating-a-mathematical-expression-in-a-string
    '''

    OPERATORS = {
        ast.Add: op.add,
        ast.Sub: op.sub,
        ast.Mult: op.mul,
        ast.Div: op.truediv, 
        ast.Pow: op.pow, 
        ast.BitXor: op.xor
    }

    def __init__(self, expression):
        self.expression = expression
        self.value = None

        if expression:
            try:
                self.value = self.evaluate(self.expression)
            except KeyboardInterrupt as e:
                raise e
            except Exception:
                pass

    def evaluate(self, expr):
        return self._eval(ast.parse(expr).body[0].value)

    def _eval(self, node):
        if isinstance(node, ast.Num): # <number>
            return node.n
        elif isinstance(node, ast.operator): # <operator>
            return self.OPERATORS[type(node)]
        elif isinstance(node, ast.BinOp): # <left> <operator> <right>
            return self._eval(node.op)(self._eval(node.left), self._eval(node.right))
        else:
            raise TypeError(node)


class BlockFile(BLOCK_FILE_PARENT_CLASS):
    '''
    Abstraction class for accessing binary files.

    This class overrides io.FilIO's read and write methods. This guaruntees two things:

        1. All requested data will be read/written via the read and write methods.
        2. All reads return a str object and all writes can accept either a str or a
           bytes object, regardless of the Python interpreter version.

    However, the downside is that other io.FileIO methods won't work properly in Python 3,
    namely things that are wrappers around self.read (e.g., readline, readlines, etc).

    This class also provides a read_block method, which is used by binwalk to read in a
    block of data, plus some additional data (MAX_TRAILING_SIZE), but on the next block read
    pick up at the end of the previous data block (not the end of the additional data). This
    is necessary for scans where a signature may span a block boundary.

    The descision to force read to return a str object instead of a bytes object is questionable
    for Python 3, it seemed the best way to abstract differences in Python 2/3 from the rest
    of the code (especially for people writing plugins) and to add Python 3 support with 
    minimal code change.
    '''

    # The MAX_TRAILING_SIZE limits the amount of data available to a signature.
    # While most headers/signatures are far less than this value, some may reference 
    # pointers in the header structure which may point well beyond the header itself.
    # Passing the entire remaining buffer to libmagic is resource intensive and will
    # significantly slow the scan; this value represents a reasonable buffer size to
    # pass to libmagic which will not drastically affect scan time.
    DEFAULT_BLOCK_PEEK_SIZE = 8 * 1024

    # Max number of bytes to process at one time. This needs to be large enough to 
    # limit disk I/O, but small enough to limit the size of processed data blocks.
    DEFAULT_BLOCK_READ_SIZE = 1 * 1024 * 1024

    def __init__(self, fname, mode='r', length=0, offset=0, block=DEFAULT_BLOCK_READ_SIZE, peek=DEFAULT_BLOCK_PEEK_SIZE, swap=0):
        '''
        Class constructor.

        @fname  - Path to the file to be opened.
        @mode   - Mode to open the file in (default: 'r').
        @length - Maximum number of bytes to read from the file via self.block_read().
        @offset - Offset at which to start reading from the file.
        @block  - Size of data block to read (excluding any trailing size),
        @peek   - Size of trailing data to append to the end of each block.
        @swap   - Swap every n bytes of data.

        Returns None.
        '''
        self.total_read = 0
        self.swap_size = swap
        self.block_read_size = self.DEFAULT_BLOCK_READ_SIZE
        self.block_peek_size = self.DEFAULT_BLOCK_PEEK_SIZE

        # Python 2.6 doesn't like modes like 'rb' or 'wb'
        mode = mode.replace('b', '')

        try:
            self.size = file_size(fname)
        except KeyboardInterrupt as e:
            raise e
        except Exception:
            self.size = 0

        if offset < 0:
            self.offset = self.size + offset
        else:
            self.offset = offset

        if self.offset < 0:
            self.offset = 0
        elif self.offset > self.size:
            self.offset = self.size

        if offset < 0:
            self.length = offset * -1
        elif length:
            self.length = length
        else:
            self.length = self.size - offset

        if self.length < 0:
            self.length = 0
        elif self.length > self.size:
            self.length = self.size

        if block is not None:
            self.block_read_size = block
        self.base_block_size = self.block_read_size
            
        if peek is not None:
            self.block_peek_size = peek
        self.base_peek_size = self.block_peek_size

        super(self.__class__, self).__init__(fname, mode)

        # Work around for python 2.6 where FileIO._name is not defined
        try:
            self.name
        except AttributeError:
            self._name = fname

        self.seek(self.offset)

    def _swap_data_block(self, block):
        '''
        Reverses every self.swap_size bytes inside the specified data block.
        Size of data block must be a multiple of self.swap_size.

        @block - The data block to swap.

        Returns a swapped string.
        '''
        i = 0
        data = ""
        
        if self.swap_size > 0:
            while i < len(block):
                data += block[i:i+self.swap_size][::-1]
                i += self.swap_size
        else:
            data = block

        return data

    def reset(self):
        self.set_block_size(block=self.base_block_size, peek=self.base_peek_size)
        self.seek(self.offset)

    def set_block_size(self, block=None, peek=None):
        if block is not None:
            self.block_read_size = block
        if peek is not None:
            self.block_peek_size = peek

    def write(self, data):
        '''
        Writes data to the opened file.
        
        io.FileIO.write does not guaruntee that all data will be written;
        this method overrides io.FileIO.write and does guaruntee that all data will be written.

        Returns the number of bytes written.
        '''
        n = 0
        l = len(data)
        data = str2bytes(data)

        while n < l:
            n += super(self.__class__, self).write(data[n:])

        return n

    def read(self, n=-1):
        ''''
        Reads up to n bytes of data (or to EOF if n is not specified).
        Will not read more than self.length bytes.

        io.FileIO.read does not guaruntee that all requested data will be read;
        this method overrides io.FileIO.read and does guaruntee that all data will be read.

        Returns a str object containing the read data.
        '''
        l = 0
        data = b''

        if self.total_read < self.length:
            # Don't read more than self.length bytes from the file
            if (self.total_read + n) > self.length:
                n = self.length - self.total_read
                
            while n < 0 or l < n:
                tmp = super(self.__class__, self).read(n-l)
                if tmp:
                    data += tmp
                    l += len(tmp)
                else:
                    break

            self.total_read += len(data)

        return self._swap_data_block(bytes2str(data))

    def peek(self, n=-1):
        '''
        Peeks at data in file.
        '''
        pos = self.tell()
        data = self.read(n)
        self.seek(pos)
        return data

    def seek(self, n, whence=os.SEEK_SET):
        if whence == os.SEEK_SET:
            self.total_read = n - self.offset
        elif whence == os.SEEK_CUR:
            self.total_read += n
        elif whence == os.SEEK_END:
            self.total_read = self.size + n

        super(self.__class__, self).seek(n, whence)

    def read_block(self):
        '''
        Reads in a block of data from the target file.

        Returns a tuple of (str(file block data), block data length).
        '''
        data = self.read(self.block_read_size)
        dlen = len(data)
        data += self.peek(self.block_peek_size)

        return (data, dlen)

