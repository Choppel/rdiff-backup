# Copyright 2003 Ben Escoto
#
# This file is part of rdiff-backup.
#
# rdiff-backup is free software; you can redistribute it and/or modify
# under the terms of the GNU General Public License as published by the
# Free Software Foundation; either version 2 of the License, or (at your
# option) any later version.
#
# rdiff-backup is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with rdiff-backup; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA 02111-1307
# USA

"""Determine the capabilities of given file system

rdiff-backup needs to read and write to file systems with varying
abilities.  For instance, some file systems and not others have ACLs,
are case-sensitive, or can store ownership information.  The code in
this module tests the file system for various features, and returns an
FSAbilities object describing it.

"""

import errno, os
import Globals, log, TempFile, selection

class FSAbilities:
	"""Store capabilities of given file system"""
	chars_to_quote = None # Hold characters not allowable in file names
	ownership = None # True if chown works on this filesystem
	acls = None # True if access control lists supported
	eas = None # True if extended attributes supported
	hardlinks = None # True if hard linking supported
	fsync_dirs = None # True if directories can be fsync'd
	dir_inc_perms = None # True if regular files can have full permissions
	resource_forks = None # True if regular_file/rsrc holds resource fork
	name = None # Short string, not used for any technical purpose
	read_only = None # True if capabilities were determined non-destructively

	def __init__(self, name = None):
		"""FSAbilities initializer.  name is only used in logging"""
		self.name = name

	def __str__(self):
		"""Return pretty printable version of self"""
		assert self.read_only == 0 or self.read_only == 1, self.read_only
		s = ['-' * 65]

		def addline(desc, val_text):
			"""Add description line to s"""
			s.append('  %s%s%s' % (desc, ' ' * (45-len(desc)), val_text))

		def add_boolean_list(pair_list):
			"""Add lines from list of (desc, boolean) pairs"""
			for desc, boolean in pair_list:
				if boolean: val_text = 'On'
				elif boolean is None: val_text = 'N/A'
				else:
					assert boolean == 0
					val_text = 'Off'
				addline(desc, val_text)			

		def get_title_line():
			"""Add the first line, mostly for decoration"""
			read_string = self.read_only and "read only" or "read/write"
			if self.name:
				return ('Detected abilities for %s (%s) file system:' %
						(self.name, read_string))
			else: return ('Detected abilities for %s file system' %
						  (read_string,))

		def add_ctq_line():
			"""Get line describing chars to quote"""
			ctq_str = (self.chars_to_quote is None and 'N/A'
					   or repr(self.chars_to_quote))
			addline('Characters needing quoting', ctq_str)

		s.append(get_title_line())
		if not self.read_only:
			add_ctq_line()
			add_boolean_list([('Ownership changing', self.ownership),
							  ('Hard linking', self.hardlinks),
							  ('fsync() directories', self.fsync_dirs),
							  ('Directory inc permissions',
							   self.dir_inc_perms)])
		add_boolean_list([('Access control lists', self.acls),
						  ('Extended attributes', self.eas),
						  ('Mac OS X style resource forks',
						   self.resource_forks)])
		s.append(s[0])
		return '\n'.join(s)

	def init_readonly(self, rp):
		"""Set variables using fs tested at RPath rp

		This method does not write to the file system at all, and
		should be run on the file system when the file system will
		only need to be read.

		Only self.acls and self.eas are set.

		"""
		self.root_rp = rp
		self.read_only = 1
		self.set_eas(rp, 0)
		self.set_acls(rp)
		self.set_resource_fork_readonly(rp)
		return self

	def init_readwrite(self, rbdir, use_ctq_file = 1,
					   override_chars_to_quote = None):
		"""Set variables using fs tested at rp_base

		This method creates a temp directory in rp_base and writes to
		it in order to test various features.  Use on a file system
		that will be written to.

		This sets self.chars_to_quote, self.ownership, self.acls,
		self.eas, self.hardlinks, and self.fsync_dirs.

		If user_ctq_file is true, try reading the "chars_to_quote"
		file in directory.

		"""
		if not rbdir.isdir():
			assert not rbdir.lstat(), (rbdir.path, rbdir.lstat())
			rbdir.mkdir()
		self.root_rp = rbdir
		self.read_only = 0

		subdir = rbdir.conn.TempFile.new_in_dir(rbdir)
		subdir.mkdir()
		self.set_ownership(subdir)
		self.set_hardlinks(subdir)
		self.set_fsync_dirs(subdir)
		self.set_eas(subdir, 1)
		self.set_acls(subdir)
		self.set_dir_inc_perms(subdir)
		self.set_resource_fork_readwrite(subdir)
		if override_chars_to_quote is None: self.set_chars_to_quote(subdir)
		else: self.chars_to_quote = override_chars_to_quote
		if use_ctq_file: self.compare_chars_to_quote(rbdir)
		subdir.delete()
		return self

	def compare_chars_to_quote(self, rbdir):
		"""Read chars_to_quote file, compare with current settings"""
		assert self.chars_to_quote is not None
		ctq_rp = rbdir.append("chars_to_quote")
		def write_new_chars():
			"""Replace old chars_to_quote file with new value"""
			if ctq_rp.lstat(): ctq_rp.delete()
			fp = ctq_rp.open("wb")
			fp.write(self.chars_to_quote)
			assert not fp.close()

		if not ctq_rp.lstat(): write_new_chars()
		else:
			old_chars = ctq_rp.get_data()
			if old_chars != self.chars_to_quote:
				if self.chars_to_quote == "":
					log.Log("Warning: File system no longer needs quoting, "
							"but will retain for backwards compatibility.", 2)
					self.chars_to_quote = old_chars
				else: log.FatalError("""New quoting requirements

This may be caused when you copy an rdiff-backup directory from a
normal file system on to a windows one that cannot support the same
characters.  If you want to risk it, remove the file
rdiff-backup-data/chars_to_quote.
""")

	def set_ownership(self, testdir):
		"""Set self.ownership to true iff testdir's ownership can be changed"""
		tmp_rp = testdir.append("foo")
		tmp_rp.touch()
		uid, gid = tmp_rp.getuidgid()
		try:
			tmp_rp.chown(uid+1, gid+1) # just choose random uid/gid
			tmp_rp.chown(0, 0)
		except (IOError, OSError), exc:
			if exc[0] == errno.EPERM:
				log.Log("Warning: ownership cannot be changed on filesystem "
						"at %s" % (self.root_rp.path,), 3)
				self.ownership = 0
			else: raise
		else: self.ownership = 1
		tmp_rp.delete()

	def set_hardlinks(self, testdir):
		"""Set self.hardlinks to true iff hard linked files can be made"""
		hl_source = testdir.append("hardlinked_file1")
		hl_dest = testdir.append("hardlinked_file2")
		hl_source.touch()
		try:
			hl_dest.hardlink(hl_source.path)
			assert hl_source.getinode() == hl_dest.getinode()
		except (IOError, OSError), exc:
			if exc[0] in (errno.EOPNOTSUPP, errno.EPERM):
				log.Log("Warning: hard linking not supported by filesystem "
						"at %s" % (self.root_rp.path,), 3)
				self.hardlinks = 0
			else: raise
		else: self.hardlinks = 1

	def set_fsync_dirs(self, testdir):
		"""Set self.fsync_dirs if directories can be fsync'd"""
		self.fsync_dirs = testdir.conn.fs_abilities.test_fsync_local(testdir)

	def set_chars_to_quote(self, subdir):
		"""Set self.chars_to_quote by trying to write various paths"""
		def is_case_sensitive():
			"""Return true if file system is case sensitive"""
			upper_a = subdir.append("A")
			upper_a.touch()
			lower_a = subdir.append("a")
			if lower_a.lstat():
				lower_a.delete()
				upper_a.setdata()
				assert not upper_a.lstat()
				return 0
			else:
				upper_a.delete()
				return 1

		def supports_unusual_chars():
			"""Test handling of several chars sometimes not supported"""
			for filename in [':', '\\', chr(175)]:
				rp = subdir.append(filename)
				try: rp.touch()
				except IOError:
					assert not rp.lstat()
					return 0
				assert rp.lstat()
				rp.delete()
			return 1

		def sanity_check():
			"""Make sure basic filenames writable"""
			for filename in ['5-_ a.']:
				rp = subdir.append(filename)
				rp.touch()
				assert rp.lstat()
				rp.delete()

		sanity_check()
		if is_case_sensitive():
			if supports_unusual_chars(): self.chars_to_quote = ""
			else: self.chars_to_quote = "^A-Za-z0-9_ -."
		else:
			if supports_unusual_chars(): self.chars_to_quote = "A-Z;"
			else: self.chars_to_quote = "^a-z0-9_ -."

	def set_acls(self, rp):
		"""Set self.acls based on rp.  Does not write.  Needs to be local"""
		self.acls = rp.conn.fs_abilities.test_acls_local(rp)
		
	def set_eas(self, rp, write):
		"""Set extended attributes from rp. Tests writing if write is true."""
		self.eas = rp.conn.fs_abilities.test_eas_local(rp, write)

	def set_dir_inc_perms(self, rp):
		"""See if increments can have full permissions like a directory"""
		test_rp = rp.append('dir_inc_check')
		test_rp.touch()
		try: test_rp.chmod(07777)
		except OSError:
			test_rp.delete()
			self.dir_inc_perms = 0
			return
		test_rp.setdata()
		assert test_rp.isreg()
		if test_rp.getperms() == 07777: self.dir_inc_perms = 1
		else: self.dir_inc_perms = 0
		test_rp.delete()

	def set_resource_fork_readwrite(self, dir_rp):
		"""Test for resource forks by writing to regular_file/rsrc"""
		reg_rp = dir_rp.append('regfile')
		reg_rp.touch()

		s = 'test string---this should end up in resource fork'
		try:
			fp_write = reg_rp.conn.open(os.path.join(reg_rp.path, 'rsrc'),
										'wb')
			fp_write.write(s)
			assert not fp_write.close()

			fp_read = reg_rp.conn.open(os.path.join(reg_rp.path, 'rsrc'), 'rb')
			s_back = fp_read.read()
			assert not fp_read.close()
		except (OSError, IOError), e: self.resource_forks = 0
		else: self.resource_forks = (s_back == s)
		reg_rp.delete()

	def set_resource_fork_readonly(self, dir_rp):
		"""Test for resource fork support by testing an regular file

		Launches search for regular file in given directory.  If no
		regular file is found, resource_fork support will be turned
		off by default.

		"""
		for rp in selection.Select(dir_rp).set_iter():
			if rp.isreg():
				try:
					rfork = rp.append('rsrc')
					fp = rfork.open('rb')
					fp.read()
					assert not fp.close()
				except (OSError, IOError), e:
					self.resource_forks = 0
					return
				self.resource_forks = 1
				return
		self.resource_forks = 0


def test_eas_local(rp, write):
	"""Test ea support.  Must be called locally.  Usedy by set_eas above."""
	assert Globals.local_connection is rp.conn
	assert rp.lstat()
	try: import xattr
	except ImportError:
		log.Log("Unable to import module xattr.  EAs not "
				"supported on filesystem at %s" % (rp.path,), 4)
		return 0

	try:
		xattr.listxattr(rp.path)
		if write:
			xattr.setxattr(rp.path, "user.test", "test val")
			assert xattr.getxattr(rp.path, "user.test") == "test val"
	except IOError, exc:
		if exc[0] == errno.EOPNOTSUPP:
			log.Log("Extended attributes not supported by "
					"filesystem at %s" % (rp.path,), 4)
			return 0
		else: raise
	else: return 1

def test_acls_local(rp):
	"""Test acl support.  Call locally.  Does not write."""
	assert Globals.local_connection is rp.conn
	assert rp.lstat()
	try: import posix1e
	except ImportError:
		log.Log("Unable to import module posix1e from pylibacl "
				"package.\nACLs not supported on filesystem at %s" %
				(rp.path,), 4)
		return 0

	try: posix1e.ACL(file=rp.path)
	except IOError, exc:
		if exc[0] == errno.EOPNOTSUPP:
			log.Log("ACLs appear not to be supported by "
					"filesystem at %s" % (rp.path,), 4)
			return 0
		else: raise
	else: return 1

def test_fsync_local(rp):
	"""Test fsyncing directories locally"""
	assert rp.conn is Globals.local_connection
	try: rp.fsync()
	except (IOError, OSError), exc:
		log.Log("Directories on file system at %s are not "
				"fsyncable.\nAssuming it's unnecessary." % (rp.path,), 4)
		return 0
	else: return 1
