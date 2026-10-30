class_name WavDecoder
extends RefCounted

## 把服务端返回的 wav 字节转成 AudioStreamWAV。
## 优先用引擎内置解析；失败时手工遍历 RIFF chunk（支持 odd-size padding）。

static func parse(bytes: PackedByteArray) -> AudioStreamWAV:
	if bytes.size() < 44:
		return null
	var builtin: AudioStreamWAV = AudioStreamWAV.load_from_buffer(bytes)
	if builtin != null:
		return builtin
	return _parse_riff(bytes)


static func _parse_riff(bytes: PackedByteArray) -> AudioStreamWAV:
	if bytes.slice(0, 4).get_string_from_ascii() != "RIFF":
		return null
	if bytes.slice(8, 12).get_string_from_ascii() != "WAVE":
		return null
	var off := 12
	var fmt_ok := false
	var channels := 1
	var sample_rate := 48000
	var bits := 16
	var pcm := PackedByteArray()
	while off + 8 <= bytes.size():
		var chunk_id := bytes.slice(off, off + 4).get_string_from_ascii()
		var size := int(bytes.decode_u32(off + 4))
		var data_off := off + 8
		if size < 0:
			break
		var end := mini(data_off + size, bytes.size())
		if chunk_id == "fmt " and size >= 16:
			var audio_format := int(bytes.decode_u16(data_off))
			channels = int(bytes.decode_u16(data_off + 2))
			sample_rate = int(bytes.decode_u32(data_off + 4))
			bits = int(bytes.decode_u16(data_off + 14))
			if audio_format != 1:          # 只接受 PCM
				return null
			fmt_ok = true
		elif chunk_id == "data":
			pcm = bytes.slice(data_off, end)
			break
		off = data_off + size + (size & 1)
	if not fmt_ok or pcm.is_empty():
		return null
	if bits != 16 or (channels != 1 and channels != 2):
		return null
	var stream := AudioStreamWAV.new()
	stream.format = AudioStreamWAV.FORMAT_16_BITS
	stream.mix_rate = sample_rate
	stream.stereo = channels == 2
	stream.data = pcm
	return stream
