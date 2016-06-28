"""
(c) Copyright 2014,2015 Hewlett-Packard Development Company, L.P.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.

Freezer general utils functions
"""
import logging
import os
import subprocess

from freezer.engine import engine
from freezer.engine.tar import tar_builders
from freezer.utils import winutils


class TarBackupEngine(engine.BackupEngine):
    DEFAULT_CHUNK_SIZE = 20000000

    def __init__(
            self, compression_algo, dereference_symlink, exclude, main_storage,
            is_windows, encrypt_pass_file=None, dry_run=False,
            chunk_size=DEFAULT_CHUNK_SIZE):
        self.compression_algo = compression_algo
        self.encrypt_pass_file = encrypt_pass_file
        self.dereference_symlink = dereference_symlink
        self.exclude = exclude
        self._main_storage = main_storage
        self.is_windows = is_windows
        self.dry_run = dry_run
        self.chunk_size = chunk_size

    @property
    def main_storage(self):
        """
        :rtype: freezer.storage.storage.Storage
        :return:
        """
        return self._main_storage

    def post_backup(self, backup, manifest):
        self.main_storage.upload_meta_file(backup, manifest)

    def backup_data(self, backup_path, manifest_path):
        logging.info("Tar engine backup stream enter")
        tar_command = tar_builders.TarCommandBuilder(
            backup_path, self.compression_algo, self.is_windows)
        if self.encrypt_pass_file:
            tar_command.set_encryption(self.encrypt_pass_file)
        if self.dereference_symlink:
            tar_command.set_dereference(self.dereference_symlink)
        tar_command.set_exclude(self.exclude)
        tar_command.set_listed_incremental(manifest_path)

        command = tar_command.build()

        logging.info("Execution command: \n{}".format(command))

        tar_process = subprocess.Popen(command, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, shell=True)
        read_pipe = tar_process.stdout
        tar_chunk = read_pipe.read(self.chunk_size)
        while tar_chunk:
            yield tar_chunk
            tar_chunk = read_pipe.read(self.chunk_size)

        self.check_process_output(tar_process, 'Backup')

        logging.info("Tar engine streaming end")

    def restore_level(self, restore_path, read_pipe, backup, except_queue):
        """
        Restore the provided file into backup_opt_dict.restore_abs_path
        Decrypt the file if backup_opt_dict.encrypt_pass_file key is provided
        """

        try:

            metadata = backup.metadata()
            if not self.encrypt_pass_file and metadata.get("encryption", False):
                raise Exception("Cannot restore encrypted backup without key")

            tar_command = tar_builders.TarCommandRestoreBuilder(
                restore_path,
                metadata.get('compression', self.compression_algo),
                self.is_windows)

            if self.encrypt_pass_file:
                tar_command.set_encryption(self.encrypt_pass_file)

            if self.dry_run:
                tar_command.set_dry_run()

            command = tar_command.build()

            if winutils.is_windows():
                # on windows, chdir to restore path.
                os.chdir(restore_path)

            tar_process = subprocess.Popen(
                command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, shell=True)
            # Start loop reading the pipe and pass the data to the tar std input.
            # If EOFError exception is raised, the loop end the std err will be
            # checked for errors.
            try:
                while True:
                    tar_process.stdin.write(read_pipe.recv_bytes())
            except EOFError:
                logging.info('[*] Pipe closed as EOF reached. '
                             'Data transmitted successfully')
            finally:
                self.check_process_output(tar_process, 'Restore')

        except Exception as e:
            except_queue.put(e)

    @staticmethod
    def check_process_output(process, function):

        """Check process stderr and return code to determine if error occurred.

        Check process stderr for any non-empty value.
        Check process return code for any non-zero value.
        Log error message and raise an exception if error occurred.

        :param process: the multiprocessing process to check
        :param function: a string: ('Restore' | 'Backup') for error message
        :returns: None -- Do nothing and return None if no error occurred
        :raises: Exception -- Raise Exception if error occurred
        """
        tar_err = process.communicate()[1]

        if tar_err:
            logging.error('{0} error: {1}'.format(function, tar_err))

        if process.returncode:
            logging.error('{0} return code is not 0'
                          .format(process.returncode))
            raise Exception('{0} process failed with return code: {1}'
                            .format(function, process.returncode))
