"""
Comprehensive test suite for photo-watcher.
Tests cover: EXIF reading, video handling, screenshot fallback, deletion sync, hash verification.
"""

import os
import shutil
import tempfile
import time
import unittest
import hashlib
from datetime import datetime
from io import BytesIO
from PIL import Image, ExifTags
from watcher import (
    get_file_date,
    get_media_date,
    get_video_metadata_date,
    is_supported_media,
    is_require_date_dir,
    get_file_hash,
    find_target_matches,
    copy_to_destination,
    remove_from_destination,
    cleanup_empty_parent_dirs,
    build_target_index,
    retry_operation,
)


class TestWatcherFixtures:
    """Helper class to create test media files."""

    @staticmethod
    def create_image_with_exif(path, exif_date_str="2025:10:15 14:30:00"):
        """Create a PNG with EXIF DateTimeOriginal."""
        img = Image.new("RGB", (100, 100), color="red")
        
        # Create EXIF data dict with DateTimeOriginal tag (0x0132 is DateTime, 0x9003 is DateTimeOriginal)
        exif_dict = {
            0x9003: exif_date_str,  # DateTimeOriginal
        }
        
        os.makedirs(os.path.dirname(path), exist_ok=True)
        img.save(path)
        
        # Re-open and inject EXIF
        img = Image.open(path)
        exif_data = img.getexif()
        exif_data[0x9003] = exif_date_str
        img.save(path, exif=exif_data)

    @staticmethod
    def create_image_no_exif(path):
        """Create a PNG without EXIF metadata."""
        img = Image.new("RGB", (100, 100), color="blue")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        img.save(path)

    @staticmethod
    def create_dummy_video(path):
        """Create a minimal MP4 file (just bytes, not a valid video)."""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(b"dummy video data")

    @staticmethod
    def set_file_mtime(path, date_str):
        """Set file modification time to a specific datetime."""
        dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
        timestamp = dt.timestamp()
        os.utime(path, (timestamp, timestamp))


class TestExifReading(unittest.TestCase):
    """Test EXIF date extraction from images."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_exif_date_extraction(self):
        """Verify EXIF DateTimeOriginal is correctly extracted."""
        img_path = os.path.join(self.temp_dir, "photo.jpg")
        expected_date_str = "2025:10:15 14:30:00"
        TestWatcherFixtures.create_image_with_exif(img_path, expected_date_str)
        
        date = get_file_date(img_path)
        self.assertIsNotNone(date)
        self.assertEqual(date.year, 2025)
        self.assertEqual(date.month, 10)
        self.assertEqual(date.day, 15)
        self.assertEqual(date.hour, 14)
        self.assertEqual(date.minute, 30)

    def test_no_exif_returns_none(self):
        """Verify that images without EXIF return None from get_file_date."""
        img_path = os.path.join(self.temp_dir, "nope.png")
        TestWatcherFixtures.create_image_no_exif(img_path)
        
        date = get_file_date(img_path)
        self.assertIsNone(date)

    def test_video_exif_check_returns_none(self):
        """Verify that get_file_date rejects video files."""
        vid_path = os.path.join(self.temp_dir, "test.mp4")
        TestWatcherFixtures.create_dummy_video(vid_path)
        
        date = get_file_date(vid_path)
        self.assertIsNone(date)


class TestMediaDateFallback(unittest.TestCase):
    """Test fallback to file mtime for videos and no-EXIF files."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_video_uses_mtime(self):
        """Verify video files use mtime for date calculation."""
        vid_path = os.path.join(self.temp_dir, "video.mp4")
        TestWatcherFixtures.create_dummy_video(vid_path)
        TestWatcherFixtures.set_file_mtime(vid_path, "2025-11-20 10:00:00")
        
        date = get_media_date(vid_path)
        self.assertIsNotNone(date)
        self.assertEqual(date.year, 2025)
        self.assertEqual(date.month, 11)
        self.assertEqual(date.day, 20)

    def test_screenshot_no_exif_uses_mtime(self):
        """Verify screenshot PNG without EXIF uses mtime."""
        ss_path = os.path.join(self.temp_dir, "screenshot.png")
        TestWatcherFixtures.create_image_no_exif(ss_path)
        TestWatcherFixtures.set_file_mtime(ss_path, "2026-01-05 15:45:30")
        
        date = get_media_date(ss_path)
        self.assertIsNotNone(date)
        self.assertEqual(date.year, 2026)
        self.assertEqual(date.month, 1)
        self.assertEqual(date.day, 5)

    def test_image_with_exif_prefers_exif(self):
        """Verify that image with EXIF uses EXIF date, not mtime."""
        img_path = os.path.join(self.temp_dir, "photo.jpg")
        TestWatcherFixtures.create_image_with_exif(img_path, "2025:05:10 12:00:00")
        # Set mtime to a different date
        TestWatcherFixtures.set_file_mtime(img_path, "2025-12-25 00:00:00")
        
        date = get_media_date(img_path)
        self.assertIsNotNone(date)
        # Should use EXIF, not mtime
        self.assertEqual(date.month, 5)
        self.assertEqual(date.day, 10)

    def test_video_without_metadata_uses_mtime(self):
        """Verify video without readable metadata falls back to mtime."""
        vid_path = os.path.join(self.temp_dir, "novideo.mp4")
        TestWatcherFixtures.create_dummy_video(vid_path)
        TestWatcherFixtures.set_file_mtime(vid_path, "2025-02-14 08:00:00")
        
        date = get_media_date(vid_path)
        self.assertIsNotNone(date)
        # Should use mtime since dummy video has no metadata
        self.assertEqual(date.year, 2025)
        self.assertEqual(date.month, 2)
        self.assertEqual(date.day, 14)


class TestSupportedFormats(unittest.TestCase):
    """Test media format detection."""

    def test_image_formats_supported(self):
        """Verify image formats are recognized."""
        for ext in [".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic"]:
            self.assertTrue(is_supported_media(f"file{ext}"))
            self.assertTrue(is_supported_media(f"FILE{ext.upper()}"))

    def test_video_formats_supported(self):
        """Verify video formats are recognized."""
        for ext in [".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"]:
            self.assertTrue(is_supported_media(f"video{ext}"))
            self.assertTrue(is_supported_media(f"VIDEO{ext.upper()}"))

    def test_unsupported_formats_rejected(self):
        """Verify unsupported formats are rejected."""
        self.assertFalse(is_supported_media("file.txt"))
        self.assertFalse(is_supported_media("doc.pdf"))
        self.assertFalse(is_supported_media("data.zip"))


class TestHashVerification(unittest.TestCase):
    """Test file hash calculation and verification."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_hash_calculation(self):
        """Verify file hash calculation works."""
        file_path = os.path.join(self.temp_dir, "test.txt")
        with open(file_path, "w") as f:
            f.write("test content")
        
        hash1 = get_file_hash(file_path)
        self.assertIsNotNone(hash1)
        self.assertEqual(len(hash1), 64)  # SHA256 hex is 64 chars

    def test_identical_files_same_hash(self):
        """Verify identical files have the same hash."""
        file1 = os.path.join(self.temp_dir, "file1.txt")
        file2 = os.path.join(self.temp_dir, "file2.txt")
        
        content = "identical content"
        with open(file1, "w") as f:
            f.write(content)
        with open(file2, "w") as f:
            f.write(content)
        
        hash1 = get_file_hash(file1)
        hash2 = get_file_hash(file2)
        self.assertEqual(hash1, hash2)

    def test_different_files_different_hash(self):
        """Verify different files have different hashes."""
        file1 = os.path.join(self.temp_dir, "file1.txt")
        file2 = os.path.join(self.temp_dir, "file2.txt")
        
        with open(file1, "w") as f:
            f.write("content1")
        with open(file2, "w") as f:
            f.write("content2")
        
        hash1 = get_file_hash(file1)
        hash2 = get_file_hash(file2)
        self.assertNotEqual(hash1, hash2)


class TestCopyFlow(unittest.TestCase):
    """Test file copying to destination with proper folder structure."""

    def setUp(self):
        self.source_dir = tempfile.mkdtemp()
        self.target_dir = tempfile.mkdtemp()
        # Patch TARGET_BASE in the watcher module
        import watcher
        self.original_target_base = watcher.TARGET_BASE
        watcher.TARGET_BASE = self.target_dir
        watcher._target_index.clear()

    def tearDown(self):
        shutil.rmtree(self.source_dir, ignore_errors=True)
        shutil.rmtree(self.target_dir, ignore_errors=True)
        import watcher
        watcher.TARGET_BASE = self.original_target_base

    def test_copy_image_with_exif(self):
        """Verify image with EXIF is copied to correct folder."""
        img_path = os.path.join(self.source_dir, "photo.jpg")
        TestWatcherFixtures.create_image_with_exif(img_path, "2025:10:15 14:30:00")
        
        copy_to_destination(img_path)
        
        # Check that file was copied to target/2025/10. Octubre 2025/
        expected_path = os.path.join(
            self.target_dir, "2025", "10. Octubre 2025", "photo.jpg"
        )
        self.assertTrue(os.path.exists(expected_path))

    def test_copy_video_uses_mtime(self):
        """Verify video is copied based on mtime."""
        vid_path = os.path.join(self.source_dir, "video.mp4")
        TestWatcherFixtures.create_dummy_video(vid_path)
        TestWatcherFixtures.set_file_mtime(vid_path, "2025-03-10 10:00:00")
        
        copy_to_destination(vid_path)
        
        expected_path = os.path.join(
            self.target_dir, "2025", "3. Marzo 2025", "video.mp4"
        )
        self.assertTrue(os.path.exists(expected_path))

    def test_copy_screenshot_no_exif(self):
        """Verify screenshot without EXIF is copied based on mtime."""
        ss_path = os.path.join(self.source_dir, "screenshot.png")
        TestWatcherFixtures.create_image_no_exif(ss_path)
        TestWatcherFixtures.set_file_mtime(ss_path, "2026-04-07 15:30:00")
        
        copy_to_destination(ss_path)
        
        expected_path = os.path.join(
            self.target_dir, "2026", "4. Abril 2026", "screenshot.png"
        )
        self.assertTrue(os.path.exists(expected_path))

    def test_no_copy_without_date(self):
        """Verify file without any usable date is skipped."""
        # This test uses a file that can't have mtime read - unlikely case
        # but we can test by passing an invalid path
        result = copy_to_destination("/nonexistent/file.jpg")
        # Should handle gracefully and return None
        self.assertIsNone(result)


class TestDeletionSync(unittest.TestCase):
    """Test safe deletion sync from source to target."""

    def setUp(self):
        self.source_dir = tempfile.mkdtemp()
        self.target_dir = tempfile.mkdtemp()
        import watcher
        self.original_target_base = watcher.TARGET_BASE
        watcher.TARGET_BASE = self.target_dir
        watcher._target_index.clear()

    def tearDown(self):
        shutil.rmtree(self.source_dir, ignore_errors=True)
        shutil.rmtree(self.target_dir, ignore_errors=True)
        import watcher
        watcher.TARGET_BASE = self.original_target_base

    def test_delete_synced_file(self):
        """Verify that deleting source file removes target copy."""
        img_path = os.path.join(self.source_dir, "photo.jpg")
        TestWatcherFixtures.create_image_with_exif(img_path, "2025:06:10 12:00:00")
        
        # Copy file
        copy_to_destination(img_path)
        target_path = os.path.join(
            self.target_dir, "2025", "6. Junio 2025", "photo.jpg"
        )
        self.assertTrue(os.path.exists(target_path))
        
        # Delete source
        os.remove(img_path)
        remove_from_destination(img_path)
        
        # Verify target is deleted
        self.assertFalse(os.path.exists(target_path))

    def test_hash_verification_protects_deletion(self):
        """Verify that hash mismatch prevents deletion."""
        img_path = os.path.join(self.source_dir, "photo.jpg")
        TestWatcherFixtures.create_image_with_exif(img_path, "2025:07:15 14:00:00")
        
        copy_to_destination(img_path)
        target_path = os.path.join(
            self.target_dir, "2025", "7. Julio 2025", "photo.jpg"
        )
        self.assertTrue(os.path.exists(target_path))
        
        # Modify target file (hash mismatch)
        with open(target_path, "ab") as f:
            f.write(b"extra data")
        
        # Try to delete source
        remove_from_destination(img_path)
        
        # Target should still exist because hash didn't match
        self.assertTrue(os.path.exists(target_path))

    def test_delete_cleans_empty_dirs(self):
        """Verify that deletion cleans up empty parent directories."""
        img_path = os.path.join(self.source_dir, "photo.jpg")
        TestWatcherFixtures.create_image_with_exif(img_path, "2025:08:20 16:00:00")
        
        copy_to_destination(img_path)
        month_dir = os.path.join(self.target_dir, "2025", "8. Agosto 2025")
        
        self.assertTrue(os.path.isdir(month_dir))
        
        # Delete source
        os.remove(img_path)
        remove_from_destination(img_path)
        
        # Month and year dirs should be cleaned up
        self.assertFalse(os.path.isdir(month_dir))
        year_dir = os.path.join(self.target_dir, "2025")
        self.assertFalse(os.path.isdir(year_dir))

    def test_find_target_matches(self):
        """Verify file discovery in target tree."""
        img_path = os.path.join(self.source_dir, "photo.jpg")
        TestWatcherFixtures.create_image_with_exif(img_path, "2025:09:10 10:00:00")
        
        copy_to_destination(img_path)
        
        matches = find_target_matches("photo.jpg")
        self.assertEqual(len(matches), 1)
        self.assertTrue(matches[0].endswith("photo.jpg"))


class TestEmptyDirCleanup(unittest.TestCase):
    """Test recursive cleanup of empty parent directories."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        import watcher
        self.original_target_base = watcher.TARGET_BASE
        watcher.TARGET_BASE = self.temp_dir
        watcher._target_index.clear()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        import watcher
        watcher.TARGET_BASE = self.original_target_base

    def test_cleanup_nested_empty_dirs(self):
        """Verify cleanup removes nested empty directories."""
        nested_path = os.path.join(self.temp_dir, "a", "b", "c", "d", "file.txt")
        os.makedirs(os.path.dirname(nested_path), exist_ok=True)
        with open(nested_path, "w") as f:
            f.write("test")
        
        os.remove(nested_path)
        cleanup_empty_parent_dirs(os.path.dirname(nested_path))
        
        # All parent dirs should be removed
        self.assertFalse(os.path.exists(os.path.join(self.temp_dir, "a", "b", "c")))

    def test_cleanup_stops_at_non_empty_dir(self):
        """Verify cleanup stops when it encounters a non-empty directory."""
        nested_path = os.path.join(self.temp_dir, "a", "b", "c")
        os.makedirs(nested_path, exist_ok=True)
        file1 = os.path.join(nested_path, "file1.txt")
        file2 = os.path.join(nested_path, "file2.txt")
        
        with open(file1, "w") as f:
            f.write("test1")
        with open(file2, "w") as f:
            f.write("test2")
        
        os.remove(file1)
        cleanup_empty_parent_dirs(os.path.dirname(file1))
        
        # Directory c should still exist because it has file2
        self.assertTrue(os.path.isdir(nested_path))


class TestMultipleSources(unittest.TestCase):
    """Integration test with multiple source directories."""

    def setUp(self):
        self.source1 = tempfile.mkdtemp()
        self.source2 = tempfile.mkdtemp()
        self.target_dir = tempfile.mkdtemp()
        import watcher
        self.original_target_base = watcher.TARGET_BASE
        watcher.TARGET_BASE = self.target_dir
        watcher._target_index.clear()

    def tearDown(self):
        shutil.rmtree(self.source1, ignore_errors=True)
        shutil.rmtree(self.source2, ignore_errors=True)
        shutil.rmtree(self.target_dir, ignore_errors=True)
        import watcher
        watcher.TARGET_BASE = self.original_target_base

    def test_copy_from_multiple_sources(self):
        """Verify files from different sources both get copied."""
        # Create in source1
        img1 = os.path.join(self.source1, "photo1.jpg")
        TestWatcherFixtures.create_image_with_exif(img1, "2025:10:01 10:00:00")
        
        # Create in source2
        img2 = os.path.join(self.source2, "photo2.jpg")
        TestWatcherFixtures.create_image_with_exif(img2, "2025:10:02 11:00:00")
        
        copy_to_destination(img1)
        copy_to_destination(img2)
        
        target1 = os.path.join(
            self.target_dir, "2025", "10. Octubre 2025", "photo1.jpg"
        )
        target2 = os.path.join(
            self.target_dir, "2025", "10. Octubre 2025", "photo2.jpg"
        )
        
        self.assertTrue(os.path.exists(target1))
        self.assertTrue(os.path.exists(target2))


class TestRequireDateDirs(unittest.TestCase):
    """Test REQUIRE_DATE_DIRS: skip mtime fallback for specified directories."""

    def setUp(self):
        self.source_dir = tempfile.mkdtemp()
        self.whatsapp_dir = tempfile.mkdtemp()
        self.target_dir = tempfile.mkdtemp()
        import watcher
        self.original_target_base = watcher.TARGET_BASE
        self.original_require_date_dirs = watcher.REQUIRE_DATE_DIRS
        watcher.TARGET_BASE = self.target_dir
        watcher.REQUIRE_DATE_DIRS = [self.whatsapp_dir]
        watcher._target_index.clear()

    def tearDown(self):
        shutil.rmtree(self.source_dir, ignore_errors=True)
        shutil.rmtree(self.whatsapp_dir, ignore_errors=True)
        shutil.rmtree(self.target_dir, ignore_errors=True)
        import watcher
        watcher.TARGET_BASE = self.original_target_base
        watcher.REQUIRE_DATE_DIRS = self.original_require_date_dirs

    def test_require_date_dir_no_exif_skipped(self):
        """File in require_date_dir without EXIF should NOT be copied."""
        img_path = os.path.join(self.whatsapp_dir, "IMG-20250410-WA0001.jpg")
        TestWatcherFixtures.create_image_no_exif(img_path)
        TestWatcherFixtures.set_file_mtime(img_path, "2026-04-10 12:00:00")

        copy_to_destination(img_path)

        # Should NOT exist anywhere in target
        matches = find_target_matches("IMG-20250410-WA0001.jpg")
        self.assertEqual(len(matches), 0)

    def test_require_date_dir_with_exif_copied(self):
        """File in require_date_dir WITH EXIF should be copied normally."""
        img_path = os.path.join(self.whatsapp_dir, "photo_wa.jpg")
        TestWatcherFixtures.create_image_with_exif(img_path, "2025:09:20 10:00:00")

        copy_to_destination(img_path)

        expected_path = os.path.join(
            self.target_dir, "2025", "9. Septiembre 2025", "photo_wa.jpg"
        )
        self.assertTrue(os.path.exists(expected_path))

    def test_non_require_date_dir_no_exif_uses_mtime(self):
        """File in normal dir without EXIF should still use mtime (unchanged behavior)."""
        img_path = os.path.join(self.source_dir, "screenshot.png")
        TestWatcherFixtures.create_image_no_exif(img_path)
        TestWatcherFixtures.set_file_mtime(img_path, "2026-04-07 15:30:00")

        copy_to_destination(img_path)

        expected_path = os.path.join(
            self.target_dir, "2026", "4. Abril 2026", "screenshot.png"
        )
        self.assertTrue(os.path.exists(expected_path))

    def test_require_date_dir_video_no_metadata_skipped(self):
        """Video in require_date_dir without metadata should NOT be copied."""
        vid_path = os.path.join(self.whatsapp_dir, "VID-20250410-WA0001.mp4")
        TestWatcherFixtures.create_dummy_video(vid_path)
        TestWatcherFixtures.set_file_mtime(vid_path, "2026-04-10 12:00:00")

        copy_to_destination(vid_path)

        matches = find_target_matches("VID-20250410-WA0001.mp4")
        self.assertEqual(len(matches), 0)

    def test_is_require_date_dir_detection(self):
        """Verify is_require_date_dir correctly identifies files."""
        file_in = os.path.join(self.whatsapp_dir, "photo.jpg")
        file_out = os.path.join(self.source_dir, "photo.jpg")

        self.assertTrue(is_require_date_dir(file_in))
        self.assertFalse(is_require_date_dir(file_out))

    def test_require_date_dir_exif_added_later(self):
        """Simulate adding EXIF to a file: first skip, then copy on modify."""
        img_path = os.path.join(self.whatsapp_dir, "wa_photo.jpg")
        TestWatcherFixtures.create_image_no_exif(img_path)

        # First attempt: no EXIF → should not copy
        copy_to_destination(img_path)
        matches = find_target_matches("wa_photo.jpg")
        self.assertEqual(len(matches), 0)

        # Simulate user adding EXIF
        TestWatcherFixtures.create_image_with_exif(img_path, "2025:03:15 09:00:00")

        # Second attempt (on_modified would trigger this): now has EXIF → should copy
        copy_to_destination(img_path)
        expected_path = os.path.join(
            self.target_dir, "2025", "3. Marzo 2025", "wa_photo.jpg"
        )
        self.assertTrue(os.path.exists(expected_path))


class TestPathTraversal(unittest.TestCase):
    """Test that path traversal attacks are blocked."""

    def setUp(self):
        self.source_dir = tempfile.mkdtemp()
        self.target_dir = tempfile.mkdtemp()
        import watcher
        self.original_target_base = watcher.TARGET_BASE
        watcher.TARGET_BASE = self.target_dir
        watcher._target_index.clear()

    def tearDown(self):
        shutil.rmtree(self.source_dir, ignore_errors=True)
        shutil.rmtree(self.target_dir, ignore_errors=True)
        import watcher
        watcher.TARGET_BASE = self.original_target_base

    def test_traversal_filename_blocked(self):
        """File with ../../ in name should not escape TARGET_BASE."""
        malicious_name = "..%2F..%2Fetc%2Fpasswd.jpg"
        img_path = os.path.join(self.source_dir, malicious_name)
        TestWatcherFixtures.create_image_with_exif(img_path, "2025:06:01 12:00:00")

        copy_to_destination(img_path)

        # File should end up inside target, not outside
        for root, _, files in os.walk(self.target_dir):
            for f in files:
                full = os.path.join(root, f)
                common = os.path.commonpath([os.path.abspath(full), os.path.abspath(self.target_dir)])
                self.assertEqual(common, os.path.abspath(self.target_dir))


class TestRetryOperation(unittest.TestCase):
    """Test retry logic with exponential backoff."""

    def test_retry_succeeds_on_second_attempt(self):
        """Verify retry succeeds after initial failure."""
        call_count = {"n": 0}

        def flaky_func():
            call_count["n"] += 1
            if call_count["n"] < 2:
                raise IOError("Temporary failure")
            return "success"

        result = retry_operation(flaky_func, max_retries=3, base_delay=0.01)
        self.assertEqual(result, "success")
        self.assertEqual(call_count["n"], 2)

    def test_retry_exhausted_raises(self):
        """Verify exception is raised after all retries exhausted."""
        def always_fail():
            raise IOError("Permanent failure")

        with self.assertRaises(IOError):
            retry_operation(always_fail, max_retries=2, base_delay=0.01)

    def test_retry_no_failure(self):
        """Verify no retry needed when function succeeds immediately."""
        call_count = {"n": 0}

        def ok_func():
            call_count["n"] += 1
            return "ok"

        result = retry_operation(ok_func, max_retries=3, base_delay=0.01)
        self.assertEqual(result, "ok")
        self.assertEqual(call_count["n"], 1)


class TestTargetIndex(unittest.TestCase):
    """Test in-memory target index."""

    def setUp(self):
        self.source_dir = tempfile.mkdtemp()
        self.target_dir = tempfile.mkdtemp()
        import watcher
        self.original_target_base = watcher.TARGET_BASE
        watcher.TARGET_BASE = self.target_dir

    def tearDown(self):
        shutil.rmtree(self.source_dir, ignore_errors=True)
        shutil.rmtree(self.target_dir, ignore_errors=True)
        import watcher
        watcher.TARGET_BASE = self.original_target_base

    def test_index_built_on_scan(self):
        """Verify build_target_index populates the index."""
        # Manually create files in target
        month_dir = os.path.join(self.target_dir, "2025", "10. Octubre 2025")
        os.makedirs(month_dir, exist_ok=True)
        with open(os.path.join(month_dir, "photo.jpg"), "w") as f:
            f.write("data")

        build_target_index()
        matches = find_target_matches("photo.jpg")
        self.assertEqual(len(matches), 1)

    def test_index_updated_on_copy(self):
        """Verify index is updated when a file is copied."""
        import watcher
        watcher._target_index.clear()

        img_path = os.path.join(self.source_dir, "indexed.jpg")
        TestWatcherFixtures.create_image_with_exif(img_path, "2025:05:01 10:00:00")

        copy_to_destination(img_path)
        matches = find_target_matches("indexed.jpg")
        self.assertEqual(len(matches), 1)

    def test_index_updated_on_delete(self):
        """Verify index is updated when a file is deleted."""
        import watcher
        watcher._target_index.clear()

        img_path = os.path.join(self.source_dir, "deleteme.jpg")
        TestWatcherFixtures.create_image_with_exif(img_path, "2025:06:01 12:00:00")
        copy_to_destination(img_path)

        self.assertEqual(len(find_target_matches("deleteme.jpg")), 1)

        os.remove(img_path)
        remove_from_destination(img_path)
        self.assertEqual(len(find_target_matches("deleteme.jpg")), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
