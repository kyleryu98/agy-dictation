"""macOS device discovery and explicit, session-scoped PCM capture."""

import ctypes
import threading
import AVFoundation as AV
import CoreMedia as CM
import Foundation as F
import objc
from ..audio_stream import SAMPLE_RATE, MAX_CHUNK
from ..ipc import RemoteError


def input_devices():
    default = AV.AVCaptureDevice.defaultDeviceWithMediaType_(AV.AVMediaTypeAudio)
    default_uid = str(default.uniqueID()) if default is not None else None
    devices = []
    for device in AV.AVCaptureDevice.devicesWithMediaType_(AV.AVMediaTypeAudio):
        name = "".join(c for c in str(device.localizedName()) if c.isprintable()).strip()
        devices.append(
            {
                "uid": str(device.uniqueID()),
                "name": name[:120] or "마이크",
                "default": str(device.uniqueID()) == default_uid,
            }
        )
    return devices


def choose_input(uid, devices=None):
    devices = input_devices() if devices is None else devices
    for device in devices:
        matches = device["uid"] == uid if uid is not None else device["default"]
        if matches:
            return device
    # An explicitly selected disconnected mic must never silently fall back.
    raise RemoteError("microphone_missing")


def sample_bytes(sample):
    description = CM.CMSampleBufferGetFormatDescription(sample)
    audio = CM.CMAudioFormatDescriptionGetStreamBasicDescription(description)
    if (
        audio.mSampleRate != SAMPLE_RATE
        or audio.mChannelsPerFrame != 1
        or audio.mBitsPerChannel != 16
        or audio.mBytesPerFrame != 2
        or audio.mFormatID != int.from_bytes(b"lpcm", "big")
        or audio.mFormatFlags & (1 | 2 | 32)  # float, big-endian or non-interleaved
        or not audio.mFormatFlags & 4  # signed integer
    ):
        raise ValueError("Unexpected capture format")
    block = CM.CMSampleBufferGetDataBuffer(sample)
    if block is None:
        raise ValueError("Capture buffer unavailable")
    size = CM.CMBlockBufferGetDataLength(block)
    if not 0 < size <= MAX_CHUNK or size % 2:
        raise ValueError("Unexpected capture size")
    error, data = CM.CMBlockBufferCopyDataBytes(block, 0, size, None)
    if error:
        raise ValueError("Capture buffer unavailable")
    return bytes(data)


class AudioSamples(F.NSObject):
    def captureOutput_didOutputSampleBuffer_fromConnection_(self, output, sample, connection):
        try:
            self.consume(sample_bytes(sample))
        except Exception:
            self.failed()


class AudioCapture:
    def __init__(self, device, consume, failed):
        self.device = device
        self.consume = consume
        self.failed = failed
        self.session = None
        self.delegate = None
        self.output = None
        self.dispatch_queue = None
        self.lock = threading.Lock()
        self.stopped = False

    def start(self):
        with self.lock, objc.autorelease_pool():
            if self.stopped:
                raise RemoteError("cancelled")
            # The engine owns permission handling. Never prompt implicitly here.
            if AV.AVCaptureDevice.authorizationStatusForMediaType_(AV.AVMediaTypeAudio) != 3:
                raise RemoteError("permission_required")
            device = AV.AVCaptureDevice.deviceWithUniqueID_(self.device["uid"])
            if device is None or not device.isConnected():
                raise RemoteError("microphone_missing")
            source, error = AV.AVCaptureDeviceInput.deviceInputWithDevice_error_(device, None)
            if source is None or error is not None:
                raise RemoteError("audio_stream_failed")
            session = AV.AVCaptureSession.alloc().init()
            output = AV.AVCaptureAudioDataOutput.alloc().init()
            output.setAudioSettings_(
                {
                    AV.AVFormatIDKey: int.from_bytes(b"lpcm", "big"),
                    AV.AVSampleRateKey: SAMPLE_RATE,
                    AV.AVNumberOfChannelsKey: 1,
                    AV.AVLinearPCMBitDepthKey: 16,
                    AV.AVLinearPCMIsFloatKey: False,
                    AV.AVLinearPCMIsBigEndianKey: False,
                    AV.AVLinearPCMIsNonInterleaved: False,
                }
            )
            if not session.canAddInput_(source) or not session.canAddOutput_(output):
                raise RemoteError("audio_stream_failed")
            session.addInput_(source)
            session.addOutput_(output)
            delegate = AudioSamples.alloc().init()
            delegate.consume, delegate.failed = self.consume, self.failed
            dispatch = ctypes.CDLL("/usr/lib/system/libdispatch.dylib")
            dispatch.dispatch_queue_create.argtypes = [ctypes.c_char_p, ctypes.c_void_p]
            dispatch.dispatch_queue_create.restype = ctypes.c_void_p
            pointer = dispatch.dispatch_queue_create(b"prolisten.audio", None)
            self.dispatch_queue = objc.objc_object(c_void_p=pointer)
            # Transfer the create reference to the PyObjC wrapper.
            dispatch.dispatch_release.argtypes = [ctypes.c_void_p]
            dispatch.dispatch_release(pointer)
            output.setSampleBufferDelegate_queue_(delegate, self.dispatch_queue)
            self.session, self.output, self.delegate = session, output, delegate
            session.startRunning()
            if not session.isRunning():
                raise RemoteError("audio_stream_failed")

    def stop(self):
        with self.lock, objc.autorelease_pool():
            self.stopped = True
            try:
                if self.session is not None:
                    self.session.stopRunning()
                if self.dispatch_queue is not None:
                    # Preserve already captured tail buffers before finalization.
                    dispatch = ctypes.CDLL("/usr/lib/system/libdispatch.dylib")
                    callback_type = ctypes.CFUNCTYPE(None, ctypes.c_void_p)
                    barrier = callback_type(lambda _: None)
                    dispatch.dispatch_sync_f.argtypes = [
                        ctypes.c_void_p, ctypes.c_void_p, callback_type
                    ]
                    dispatch.dispatch_sync_f.restype = None
                    dispatch.dispatch_sync_f(objc.pyobjc_id(self.dispatch_queue), None, barrier)
            finally:
                if self.output is not None:
                    self.output.setSampleBufferDelegate_queue_(None, None)
                self.session = self.output = self.delegate = self.dispatch_queue = None
