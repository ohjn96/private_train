# -*- coding: utf-8 -*-
"""iOS API 호출 (rubicon-objc). 아이폰/시뮬레이터에서만 import 한다.

UIKit 을 만지는 함수(set_idle_timer_disabled, notify)는 메인 스레드에서 불러야 한다
(IOSBridge 가 call_on_main 으로 넘겨 준다). 모두 "최선을 다하되 실패해도 앱은 계속" 이다.

알림은 UNUserNotificationCenter 로 띄운다:
- 앱을 켤 때 권한을 한 번 묻는다 (거절하면 조용히 넘어간다. 웹 화면에도 결과 배너가 뜬다).
- 앱이 화면에 떠 있을 때도 배너가 보이도록 delegate 의 willPresentNotification 에서
  배너·목록·소리를 허용한다. delegate 는 약한 참조라 이 모듈이 붙잡아 둔다.

백그라운드 유지(AudioKeepAlive): 매크로가 도는 동안 소리 없는 파일을 무한 반복 재생한다
(AVAudioSession Playback + MixWithOthers, Info.plist 의 UIBackgroundModes=audio).
상태 판단은 ios_bridge.BackgroundKeeper 가 하고, 여기는 AVFoundation 호출만 한다.
"""
import itertools
import logging

from pathlib import Path

from rubicon.objc import SEL, Block, NSObject, NSUInteger, ObjCBlock, ObjCClass, ObjCProtocol, objc_id, objc_method
from rubicon.objc.api import ns_from_py, objc_const
from rubicon.objc.runtime import load_library

logger = logging.getLogger(__name__)

UIApplication = ObjCClass('UIApplication')
UIDevice = ObjCClass('UIDevice')
NSFileManager = ObjCClass('NSFileManager')
NSURL = ObjCClass('NSURL')

# UNAuthorizationOptions
_AUTH_BADGE, _AUTH_SOUND, _AUTH_ALERT = 1 << 0, 1 << 1, 1 << 2
# UNNotificationPresentationOptions
_PRESENT_SOUND, _PRESENT_ALERT, _PRESENT_LIST, _PRESENT_BANNER = 1 << 1, 1 << 2, 1 << 3, 1 << 4
# UIApplicationState
_STATE_ACTIVE = 0

_center_state = {'center': None, 'delegate': None, 'blocks': []}
_ids = itertools.count(1)


def _ios_major() -> int:
    try:
        return int(str(UIDevice.currentDevice.systemVersion).split('.')[0])
    except Exception:  # noqa: BLE001
        return 0


def _presentation_options() -> int:
    if _ios_major() >= 14:
        return _PRESENT_BANNER | _PRESENT_LIST | _PRESENT_SOUND
    return _PRESENT_ALERT | _PRESENT_SOUND


def _make_delegate_class():
    """UNUserNotificationCenterDelegate. 프로토콜을 못 찾으면 프로토콜 선언 없이 만든다
    (UNUserNotificationCenter 는 respondsToSelector 로 메서드를 찾으므로 그래도 동작한다)."""
    try:
        protocols = [ObjCProtocol('UNUserNotificationCenterDelegate')]
    except Exception:  # noqa: BLE001
        logger.warning('UNUserNotificationCenterDelegate protocol not found; declaring without it')
        protocols = []

    class TrainNotificationDelegate(NSObject, protocols=protocols):
        @objc_method
        def userNotificationCenter_willPresentNotification_withCompletionHandler_(
            self, center, notification, completionHandler
        ) -> None:
            # 앱이 앞에 있어도 배너로 보여준다
            ObjCBlock(completionHandler, None, NSUInteger)(_presentation_options())

        @objc_method
        def userNotificationCenter_didReceiveNotificationResponse_withCompletionHandler_(
            self, center, response, completionHandler
        ) -> None:
            # 알림을 누르면 앱이 열리기만 하면 된다
            ObjCBlock(completionHandler, None)()

    return TrainNotificationDelegate


def setup_notifications() -> None:
    """앱을 켤 때 한 번 (메인 스레드). 권한을 묻고 delegate 를 단다."""
    try:
        load_library('UserNotifications')
        center = ObjCClass('UNUserNotificationCenter').currentNotificationCenter()
        delegate = _make_delegate_class().alloc().init()
        center.delegate = delegate
        _center_state.update(center=center, delegate=delegate)

        def done(granted: bool, error: objc_id) -> None:
            logger.info('notification permission granted=%s', bool(granted))

        block = Block(done, None, bool, objc_id)
        _center_state['blocks'].append(block)  # 콜백이 불릴 때까지 살려 둔다
        center.requestAuthorizationWithOptions_completionHandler_(
            _AUTH_ALERT | _AUTH_SOUND | _AUTH_BADGE, block
        )
    except Exception:  # noqa: BLE001
        logger.exception('notification setup failed; notifications disabled')
        _center_state['center'] = None


def notify(kind: str, title: str, body: str) -> None:
    """로컬 알림 하나 (메인 스레드). 권한이 없으면 iOS 가 조용히 버린다."""
    center = _center_state['center']
    if center is None:
        return
    content = ObjCClass('UNMutableNotificationContent').alloc().init()
    content.title = title
    content.body = body
    content.sound = ObjCClass('UNNotificationSound').defaultSound
    content.threadIdentifier = 'macro'
    request = ObjCClass('UNNotificationRequest').requestWithIdentifier_content_trigger_(
        f'{kind}-{next(_ids)}', content, None  # trigger 가 없으면 바로 띄운다
    )
    center.addNotificationRequest_withCompletionHandler_(request, None)


def set_idle_timer_disabled(disabled: bool) -> None:
    """True 면 화면이 저절로 잠기지 않는다 (메인 스레드)."""
    UIApplication.sharedApplication.idleTimerDisabled = bool(disabled)


def is_active() -> bool:
    try:
        return int(UIApplication.sharedApplication.applicationState) == _STATE_ACTIVE
    except Exception:  # noqa: BLE001
        return True


def protect_file(path) -> None:
    """NSFileProtectionComplete: 폰이 잠겨 있는 동안엔 이 파일을 읽을 수 없다 (디스크 암호화 키가 내려감).
    앱은 화면에 떠 있을 때(=잠금 해제 상태)만 이 파일을 읽고 쓰므로 문제없다."""
    ok = NSFileManager.defaultManager.setAttributes_ofItemAtPath_error_(
        ns_from_py({'NSFileProtectionKey': 'NSFileProtectionComplete'}), str(path), None
    )
    if not ok:
        raise OSError(f'setAttributes failed for {path}')


def exclude_from_backup(path) -> None:
    """iCloud/PC 백업에 코레일 비밀번호·카드가 담긴 작업 파일이 들어가지 않게."""
    url = NSURL.fileURLWithPath(str(path))
    ok = url.setResourceValue_forKey_error_(ns_from_py(True), 'NSURLIsExcludedFromBackupKey', None)
    if not ok:
        raise OSError(f'exclude from backup failed for {path}')


def open_external(url: str) -> None:
    """바깥 링크는 사파리로 (메인 스레드). WebView 안에서는 우리 서버만 연다."""
    ns_url = NSURL.URLWithString(url)
    if ns_url is None:
        return
    UIApplication.sharedApplication.openURL_options_completionHandler_(ns_url, ns_from_py({}), None)


# ── 백그라운드 유지 (소리 없는 오디오)

#: 앱에 싣는 소리 없는 파일 (1초, 8kHz 모노 16비트, 샘플이 전부 0). 패키지 폴더 안에 있어야 앱에 실린다.
SILENCE_FILE = Path(__file__).resolve().parent / 'resources' / 'silence.wav'

_MIX_WITH_OTHERS = 0x1              # AVAudioSessionCategoryOptionMixWithOthers
_NOTIFY_OTHERS_ON_DEACTIVATION = 0x1  # AVAudioSessionSetActiveOptionNotifyOthersOnDeactivation
_INTERRUPTION_BEGAN = 1             # AVAudioSessionInterruptionTypeBegan (Ended = 0)

_avf = {}


def _avfoundation():
    """AVFoundation 을 한 번 불러 클래스·상수를 돌려준다. 상수를 못 찾으면 이름 문자열을 쓴다
    (이 상수들의 값은 이름과 같은 NSString 이다)."""
    if not _avf:
        lib = load_library('AVFoundation')

        def const(name):
            try:
                return objc_const(lib, name)
            except Exception:  # noqa: BLE001
                return name

        _avf.update(
            session=ObjCClass('AVAudioSession'),
            player=ObjCClass('AVAudioPlayer'),
            playback=const('AVAudioSessionCategoryPlayback'),
            interruption=const('AVAudioSessionInterruptionNotification'),
            reset=const('AVAudioSessionMediaServicesWereResetNotification'),
            type_key=const('AVAudioSessionInterruptionTypeKey'),
        )
    return _avf


_observer_class = []


def _make_observer_class():
    """NSNotificationCenter 가 부를 옵저버. 클래스는 한 번만 등록할 수 있어 붙잡아 둔다."""
    if _observer_class:
        return _observer_class[0]

    class TrainAudioObserver(NSObject):
        @objc_method
        def interruption_(self, notification) -> None:
            _dispatch_audio_event('interruption', notification)

        @objc_method
        def mediaReset_(self, notification) -> None:
            _dispatch_audio_event('reset', notification)

    _observer_class.append(TrainAudioObserver)
    return TrainAudioObserver


_audio_handlers = []


def _dispatch_audio_event(kind, notification) -> None:
    """iOS 가 부르는 스레드(메인이 아닐 수 있음)에서 온다. 파이썬 쪽 handler 는 스레드와 무관해야 한다."""
    try:
        began = None
        if kind == 'interruption':
            info = notification.userInfo
            value = info.objectForKey_(_avfoundation()['type_key']) if info is not None else None
            began = value is not None and int(value.unsignedIntegerValue) == _INTERRUPTION_BEGAN
        for handler in list(_audio_handlers):
            handler(kind, began)
    except Exception:  # noqa: BLE001 - ObjC 콜백에서 예외가 새면 앱이 죽는다
        logger.exception('audio session event failed')


class AudioKeepAlive:
    """소리 없는 파일을 무한 반복 재생해 앱을 깨워 둔다 (메인 스레드).

    on_event(kind, began): 'interruption'(began=True/False), 'reset'(began=None).
    아무 스레드에서나 불리므로 app.py 가 메인 스레드로 넘긴다.
    start() 는 실패하면 예외를 던진다 (BackgroundKeeper 가 받아 로그를 남기고 화면 켜 두기로 돌아간다).
    """

    def __init__(self, sound_path=SILENCE_FILE, on_event=None):
        self.sound_path = Path(sound_path)
        self._on_event = on_event
        self._player = None
        self._observer = None

    def start(self) -> None:
        avf = _avfoundation()
        session = avf['session'].sharedInstance()
        self._observe(session, avf)
        if not session.setCategory_withOptions_error_(avf['playback'], _MIX_WITH_OTHERS, None):
            raise OSError('AVAudioSession setCategory failed')
        if not session.setActive_error_(True, None):
            raise OSError('AVAudioSession setActive failed')
        if self._player is None:
            if not self.sound_path.is_file():
                raise FileNotFoundError(self.sound_path)
            url = NSURL.fileURLWithPath(str(self.sound_path))
            player = avf['player'].alloc().initWithContentsOfURL_error_(url, None)
            if player is None:
                raise OSError('AVAudioPlayer init failed')
            player.numberOfLoops = -1  # 무한 반복
            player.prepareToPlay()
            self._player = player
        if not self._player.play():
            raise OSError('AVAudioPlayer play failed')
        logger.info('background keep-alive audio playing')

    def stop(self) -> None:
        player, self._player = self._player, None
        if player is not None:
            player.stop()
        try:
            session = _avfoundation()['session'].sharedInstance()
            session.setActive_withOptions_error_(False, _NOTIFY_OTHERS_ON_DEACTIVATION, None)
        except Exception:  # noqa: BLE001
            logger.warning('AVAudioSession deactivate failed', exc_info=True)
        logger.info('background keep-alive audio stopped')

    def _observe(self, session, avf) -> None:
        if self._observer is not None:
            return
        observer = _make_observer_class().alloc().init()
        center = ObjCClass('NSNotificationCenter').defaultCenter
        center.addObserver_selector_name_object_(observer, SEL('interruption:'), avf['interruption'], session)
        center.addObserver_selector_name_object_(observer, SEL('mediaReset:'), avf['reset'], session)
        self._observer = observer  # 알림 센터는 옵저버를 붙잡지 않는다
        if self._on_event is not None:
            _audio_handlers.append(self._on_event)


class NativeAPI:
    """IOSBridge 에 넘기는 native 객체."""

    set_idle_timer_disabled = staticmethod(set_idle_timer_disabled)
    notify = staticmethod(notify)
