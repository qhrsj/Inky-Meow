@tool
extends "res://addons/godot_ai/testing/test_suite.gd"

const WD_SCRIPT := preload("res://scripts/util/wav_decoder.gd")


func suite_name() -> String:
	return "wav_decoder"


func _put_ascii(buf: PackedByteArray, off: int, s: String) -> void:
	for i in range(s.length()):
		buf.encode_u8(off + i, s.unicode_at(i))


func _make_wav() -> PackedByteArray:
	var pcm := PackedByteArray()
	pcm.resize(4)
	pcm.encode_u16(0, 0x1234)
	pcm.encode_u16(2, 0x5678)
	var b := PackedByteArray()
	b.resize(44 + pcm.size())
	_put_ascii(b, 0, "RIFF")
	b.encode_u32(4, 36 + pcm.size())
	_put_ascii(b, 8, "WAVE")
	_put_ascii(b, 12, "fmt ")
	b.encode_u32(16, 16)
	b.encode_u16(20, 1)          # PCM
	b.encode_u16(22, 1)          # mono
	b.encode_u32(24, 48000)
	b.encode_u32(28, 96000)      # byte rate
	b.encode_u16(32, 2)          # block align
	b.encode_u16(34, 16)         # bits
	_put_ascii(b, 36, "data")
	b.encode_u32(40, pcm.size())
	for i in range(pcm.size()):
		b.encode_u8(44 + i, pcm[i])
	return b


func test_manual_riff_parse() -> void:
	var s: AudioStreamWAV = WD_SCRIPT._parse_riff(_make_wav())
	assert_true(s != null, "手工 RIFF 解析应成功")
	if s != null:
		assert_eq(s.format, AudioStreamWAV.FORMAT_16_BITS)
		assert_eq(s.mix_rate, 48000)
		assert_false(s.stereo)
		assert_eq(s.data.size(), 4)


func test_parse_accepts_valid_wav() -> void:
	var s: AudioStreamWAV = WD_SCRIPT.parse(_make_wav())
	assert_true(s != null, "parse 应返回 AudioStreamWAV")
	if s != null:
		assert_eq(s.mix_rate, 48000)


func test_parse_rejects_short() -> void:
	var b := PackedByteArray()
	b.resize(10)
	assert_true(WD_SCRIPT.parse(b) == null, "短数据应返回 null")
