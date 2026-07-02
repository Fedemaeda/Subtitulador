"""
Tests for Subtitulador core functions
"""

import pytest
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import detect_silence, _is_in_silence, _is_hallucination, _tighten_timings, generate_srt


class TestDetectSilence:
    """Test silence detection"""
    
    def test_returns_list(self):
        """Should return a list"""
        # This would need an actual audio file, so we just test the structure
        result = detect_silence("nonexistent.wav")
        assert isinstance(result, list)


class TestIsInSilence:
    """Test silence overlap detection"""
    
    def test_no_overlap(self):
        """Should return False when no silence overlaps"""
        silences = [(1.0, 2.0), (3.0, 4.0)]
        result = _is_in_silence(5.0, 6.0, silences)
        assert result == False
    
    def test_full_overlap(self):
        """Should return True when fully inside silence"""
        silences = [(1.0, 3.0)]
        result = _is_in_silence(1.5, 2.5, silences)
        assert result == True
    
    def test_partial_overlap(self):
        """Should return True when overlap > 50%"""
        silences = [(1.0, 2.0)]
        # Segment 1.0-1.8, silence 1.0-2.0, overlap = 0.8/1.0 = 80% > 50%
        result = _is_in_silence(1.0, 1.8, silences)
        assert result == True
    
    def test_small_overlap(self):
        """Should return False when overlap < 50%"""
        silences = [(1.0, 2.0)]
        # Segment 0.5-1.3, silence 1.0-2.0, overlap = 0.3/0.8 = 37.5% < 50%
        result = _is_in_silence(0.5, 1.3, silences)
        assert result == False


class TestIsHallucination:
    """Test hallucination filtering"""
    
    def test_short_duration_filtered(self):
        """Should filter segments shorter than min_duration"""
        segments = [
            {"start": 0.0, "end": 0.2, "text": "Hi"},
            {"start": 1.0, "end": 2.0, "text": "Hello"}
        ]
        result = _is_hallucination(segments, min_duration=0.3)
        assert len(result) == 1
        assert result[0]["text"] == "Hello"
    
    def test_empty_text_filtered(self):
        """Should filter empty text"""
        segments = [
            {"start": 0.0, "end": 1.0, "text": ""},
            {"start": 1.0, "end": 2.0, "text": "Valid"}
        ]
        result = _is_hallucination(segments)
        assert len(result) == 1
    
    def test_thank_you_filtered(self):
        """Should filter 'thank you' patterns"""
        segments = [
            {"start": 0.0, "end": 1.0, "text": "Thank you for watching"},
            {"start": 1.0, "end": 2.0, "text": "Hello world"}
        ]
        result = _is_hallucination(segments)
        assert len(result) == 1
        assert result[0]["text"] == "Hello world"
    
    def test_repeated_text_filtered(self):
        """Should filter repeated text"""
        segments = [
            {"start": 0.0, "end": 1.0, "text": "Same"},
            {"start": 1.0, "end": 2.0, "text": "Same"},
            {"start": 2.0, "end": 3.0, "text": "Same"},
            {"start": 3.0, "end": 4.0, "text": "Same"},
            {"start": 4.0, "end": 5.0, "text": "Different"}
        ]
        result = _is_hallucination(segments, max_repeats=3)
        # With max_repeats=3, first 3 "Same" are kept, 4th is filtered
        assert len(result) == 4


class TestTightenTimings:
    """Test timing adjustment"""
    
    def test_no_silences(self):
        """Should return segments unchanged when no silences"""
        segments = [
            {"start": 0.0, "end": 1.0, "text": "Hello"}
        ]
        result = _tighten_timings(segments, [])
        assert result == segments
    
    def test_segment_in_silence_removed(self):
        """Should remove segments fully in silence"""
        segments = [
            {"start": 1.5, "end": 1.8, "text": "Test"}
        ]
        silences = [(1.0, 2.0)]
        result = _tighten_timings(segments, silences)
        assert len(result) == 0
    
    def test_segment_trimmed_at_silence(self):
        """Should trim segment at silence boundary"""
        segments = [
            {"start": 0.0, "end": 2.0, "text": "Hello"}
        ]
        silences = [(1.0, 2.0)]
        result = _tighten_timings(segments, silences)
        assert len(result) == 1
        # Should be trimmed before silence (at 0.9)
        assert result[0]["end"] < 1.0


class TestGenerateSRT:
    """Test SRT generation"""
    
    def test_basic_srt(self):
        """Should generate valid SRT format"""
        segments = [
            {"start": 0.0, "end": 1.0, "text": "Hello"},
            {"start": 1.5, "end": 2.5, "text": "World"}
        ]
        srt = generate_srt(segments)
        
        # Check format
        assert "1\n" in srt
        assert "00:00:00,000 --> 00:00:01,000" in srt
        assert "Hello" in srt
        assert "2\n" in srt
        assert "World" in srt
    
    def test_empty_segments(self):
        """Should handle empty segments"""
        srt = generate_srt([])
        assert srt == ""


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
