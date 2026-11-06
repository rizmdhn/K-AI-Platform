"""Legacy exception imports remain compatible; approval writing is removed."""
import unittest
import approval_store as store

class StorageErrorTests(unittest.TestCase):
    def test_error_compatibility_and_fixed_sqlstate_mapping(self):
        for state,status in (('K0001',403),('K0002',409),('K0003',400),('08000',503)):
            error=store.storage_error(type('Failure',(),{'sqlstate':state})(),
                                      {403:'denied',409:'changed',400:'invalid',503:'unavailable'})
            self.assertIsInstance(error,store.ApprovalError)
            self.assertEqual(error.status,status)

    def test_approval_write_api_is_removed(self):
        self.assertFalse(hasattr(store,'record_approval'))
        self.assertFalse(hasattr(store,'ApprovalRequest'))
