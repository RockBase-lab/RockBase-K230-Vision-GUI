# String tables and language state for the whole GUI.
#
# NAMES holds one (en, zh) entry per app id; STRINGS holds one per user-visible
# app string, keyed "<app>.<purpose>" ("common.*" when several apps share it).
# Every zh half must stay inside tools/ui_cjk_charset.txt, which
# gen_ui_cjk_charset.py regenerates from this file; both ui_cjk fonts bake from
# that exact set, so a character outside it renders as a box on board.
#
# Language changes are hot: set_lang() persists to config and raises a flag the
# launcher main loop consumes to rebuild the home screen (tile bitmaps are baked
# per language, see cache_path in shell/tile_cache.py). Apps read strings at
# build time, so an already-open app keeps its language; Settings rebuilds itself.

from core import config

LANGS = ("en", "zh")
CJK_FONT = "ui_cjk_28.bin"
CJK_FONT_16 = "ui_cjk_16.bin"

NAMES = {
    # Top-level tiles
    "camera": ("Camera", "相机"),
    "gallery": ("Gallery", "相册"),
    "scan": ("Scan", "扫码"),
    "face": ("Face", "人脸"),
    "vision": ("Vision", "视觉"),
    "audio": ("Audio", "音频"),
    "touch": ("Touch", "触控"),
    "imu": ("IMU", "惯导"),
    "network": ("Network", "网络"),
    "settings": ("Settings", "设置"),
    "battery": ("Battery", "电量"),
    # Vision folder members
    "detect": ("Detect", "检测"),
    "pose": ("Pose", "骨骼"),
    "segment": ("Segment", "分割"),
    "orient": ("Orient", "朝向"),
    "person": ("Person", "行人"),
    "fall": ("Fall", "摔倒"),
    "gesture": ("Gesture", "手势"),
    "plate": ("Plate", "车牌"),
    "ocr": ("OCR", "文字"),
    # Face folder members
    "face_det": ("Faces", "人脸"),
    "landmark": ("Points", "关键点"),
    "recognize": ("Recognize", "识别"),
    "liveness": ("Liveness", "活体"),
    "mask": ("Mask", "口罩"),
}

# App strings. Templates keep their {} placeholders in both halves and are
# formatted after t() returns.
STRINGS = {
    # Shared
    "common.unavailable": ("{} unavailable", "{} 不可用"),
    # en half mirrors errors.CAMERA_UNAVAILABLE
    "common.camera_unavailable": ("Camera module not detected.\nThis app cannot run without the camera.\nCheck the camera cable and try again.", "未检测到摄像头模组。\n此应用需要摄像头才能运行。\n请检查摄像头排线后重试。"),
    "common.start_failed": ("This app could not start.\n\n{}", "应用启动失败。\n\n{}"),
    # en half mirrors BATTERY_UNAVAILABLE in apps/app_battery.py
    "common.battery_unavailable": ("Battery ADC does not respond.\nThe pack voltage cannot be measured.\nRestart the board and try again.", "电池 ADC 无响应。\n无法测量电池电压。\n请重启开发板后重试。"),
    "common.cancel": ("Cancel", "取消"),
    # Boot splash
    "boot.preparing": ("Preparing desktop", "正在准备桌面"),
    "boot.tiles": ("tile cache", "磁贴缓存"),
    "boot.tiles_done": ("tiles ready", "磁贴就绪"),
    "boot.building": ("building home", "正在构建桌面"),
    "boot.fonts": ("Loading fonts {}/{}", "正在加载字体 {}/{}"),
    "boot.dict": ("Loading text dictionary", "正在加载文本词典"),
    "boot.models": ("Loading models {}/{}", "正在加载模型 {}/{}"),
    "boot.wifi": ("Connecting to Wi-Fi", "正在连接 Wi-Fi"),
    "boot.wifi_hint": ("one attempt, up to {} s", "单次尝试,最长 {} 秒"),
    "boot.fonts_ondemand": ("fonts: on demand", "字体:按需加载"),
    "boot.wifi_connected": ("Wi-Fi: connected {}", "Wi-Fi:已连接 {}"),
    "boot.wifi_ok": ("Wi-Fi: connected", "Wi-Fi:已连接"),
    "boot.wifi_notconfig": ("Wi-Fi: not configured", "Wi-Fi:未配置"),
    "boot.wifi_failed": ("Wi-Fi: not connected", "Wi-Fi:未连接"),
    # Shared AI-app status bar
    "ai.loading": ("loading", "加载中"),
    "ai.objs": ("obj {}", "目标 {}"),
    "ai.frozen": ("FROZEN", "已冻结"),
    "ai.tap_freeze": ("TAP to freeze", "点按冻结"),
    "ai.snapshot_error": ("snapshot error", "取帧错误"),
    "ai.display_error": ("display error", "显示错误"),
    "ai.no_face": ("no face", "无人脸"),
    "ai.tap_switch": ("TAP to switch", "点按切换"),
    "ai.select_model": ("Select model", "选择模型"),
    "ai.select_view": ("Select view", "选择视图"),
    "ai.row_switch": ("tap a row to switch, tap outside to close", "点行切换,点外侧关闭"),
    "ai.row_apply": ("tap a row to apply, tap outside to close", "点行应用,点外侧关闭"),
    "ai.row_delete": ("tap a row to delete, tap outside to close", "点行删除,点外侧关闭"),
    "ai.model_load_failed": ("model load failed", "模型加载失败"),
    "ai.loading_dots": ("LOADING...", "加载中..."),
    "ai.reading": ("READING...", "识别中..."),
    "ai.tap_scan": ("TAP scan", "点按扫描"),
    "ai.tap_resume": ("TAP resume", "点按继续"),
    "ai.tap_threshold": ("TAP: threshold", "点按:阈值"),
    "ai.tap_view": ("TAP: view", "点按:视图"),
    "ai.lines": ("{} lines", "{} 行"),
    "ai.no_lines": ("0 lines", "0 行"),
    "ai.plates": ("{} plate", "车牌 {}"),
    "ai.no_plates": ("0 plate", "无车牌"),
    "ai.persons": ("{} person", "{} 人"),
    "ai.face_one": ("1 face", "1 张人脸"),
    "ai.pts": ("{} pts", "{} 点"),
    "ai.show_hand": ("show a hand", "请展示手掌"),
    "ai.voice_off": ("VOICE OFF", "语音关"),
    "ai.measuring": ("measuring", "测量中"),
    "ai.ok_freeze": ("OK: freeze", "OK: 冻结"),
    "ai.det_threshold": ("Detection threshold", "检测阈值"),
    "ai.liveness_threshold": ("Liveness threshold", "活体阈值"),
    # Threshold level names (the number is part of the display name)
    "ai.th.recall_025": ("Recall 0.25", "召回优先 0.25"),
    "ai.th.lenient_030": ("Lenient 0.30", "宽松 0.30"),
    "ai.th.normal_050": ("Normal 0.50", "常规 0.50"),
    "ai.th.strict_070": ("Strict 0.70", "严格 0.70"),
    "ai.th.strict_075": ("Strict 0.75", "严格 0.75"),
    # Face apps
    "face.title": ("Faces {:.2f}", "人脸 {:.2f}"),
    "face.count": ("{}{} face{}", "{}{} 张人脸"),
    "face.box": ("FACE", "人脸"),
    "face.box_n": ("#{} FACE", "#{} 人脸"),
    "liveness.title": ("Liveness {:.2f}", "活体 {:.2f}"),
    "liveness.real": ("REAL {:.2f}", "真人 {:.2f}"),
    "liveness.spoof": ("SPOOF {:.2f}", "假体 {:.2f}"),
    "points.title": ("Points {}", "关键点 {}"),
    "points.view.mesh": ("Mesh", "网格"),
    "points.view.contour": ("Contour", "轮廓"),
    "points.view.off": ("Off", "关闭"),
    # Gesture names (classify() emits the English tokens)
    "gesture.fist": ("fist", "拳头"),
    "gesture.five": ("five", "五指"),
    "gesture.gun": ("gun", "手枪"),
    "gesture.love": ("love", "爱你"),
    "gesture.one": ("one", "一"),
    "gesture.six": ("six", "六"),
    "gesture.three": ("three", "三"),
    "gesture.thumbup": ("thumbUp", "点赞"),
    "gesture.yeah": ("yeah", "耶"),
    # Pose view picker (name|note pairs per row)
    "pose.hint_tap": ("{} · TAP", "{} · 点按"),
    "pose.view.0.name": ("Skeleton", "骨架"),
    "pose.view.0.note": ("bones + joints", "骨骼线+关节点"),
    "pose.view.1.name": ("Bones", "骨骼线"),
    "pose.view.1.note": ("no joint dots", "无关节点"),
    "pose.view.2.name": ("Off", "关闭"),
    "pose.view.2.note": ("preview only", "仅预览"),
    "ai.detect_error": ("detect error", "检测错误"),
    # en half mirrors the AppUnavailable payload in apps/app_segment.py
    "common.seg_buf_unavailable": ("Segmentation buffer too large for this build.", "分割缓冲过大,此固件无法运行。"),
    "ai.snapshot_error_fmt": ("snapshot error: {}", "取帧错误: {}"),
    # Camera
    "camera.init": ("camera init...", "摄像头初始化中..."),
    "camera.saving": ("Saving...", "保存中..."),
    "camera.saved": ("Saved\n{}\n{}x{}  {}KB", "已保存\n{}\n{}x{}  {}KB"),
    "camera.save_failed": ("Save failed:\n{}", "保存失败:\n{}"),
    # Scan
    "scan.hint": ("scan: point at a QR code", "对准二维码扫描"),
    "scan.hint_fps": ("scan: point at a QR code | FPS {:.1f}", "对准二维码扫描 | FPS {:.1f}"),
    "scan.title": ("QR Code", "二维码"),
    "scan.again": ("SCAN AGAIN", "再次扫描"),
    # Touch (fallback label; the visible hint is a baked PNG)
    "touch.hint": ("5-point multi-touch", "支持 5 点触控"),
    # Battery (status constants stay English internally; these are display text)
    "battery.charge": ("CHARGE", "电量"),
    "battery.charge_note": ("charge from pack voltage", "由电池电压估算"),
    "battery.measurement": ("MEASUREMENT", "测量数据"),
    "battery.row.tap": ("DIVIDER TAP", "分压抽头"),
    "battery.row.code": ("ADC CODE", "ADC 码值"),
    "battery.row.span": ("SAMPLE SPAN", "采样极差"),
    "battery.row.rejected": ("REJECTED", "已丢弃"),
    "battery.row.samples": ("SAMPLES", "采样数"),
    "battery.last_secs": ("LAST {} s", "最近 {} 秒"),
    "battery.measuring": ("MEASURING", "测量中"),
    "battery.present": ("BATTERY PRESENT", "电池在位"),
    "battery.charging": ("CHARGING", "充电中"),
    "battery.absent": ("NO BATTERY", "无电池"),
    "battery.unstable": ("UNSTABLE INPUT", "输入不稳"),
    "battery.range": ("OUT OF RANGE", "超出范围"),
    "battery.adc_error": ("ADC ERROR", "ADC 错误"),
    # Gallery
    "gallery.empty": ("No photos\n\n", "暂无照片\n\n"),
    "gallery.count": ("{} photos", "共 {} 张照片"),
    # Audio
    "audio.again": ("record again", "重新录制"),
    "audio.hint_rec": ("tap to record, up to {}", "点按录音,最长 {}"),
    "audio.hint_stop_rec": ("tap STOP when done", "完成后点停止"),
    "audio.hint_processing": ("processing, please wait", "正在处理,请稍候"),
    "audio.hint_stop_play": ("tap STOP to stop", "点停止结束播放"),
    "audio.sub_none": ("no recording yet", "还没有录音"),
    "audio.sub_rec": ("recording...", "录音中..."),
    "audio.sub_saving": ("saving...", "保存中..."),
    "audio.sub_ready": ("recorded {}", "已录制 {}"),
    "audio.sub_playing": ("playing...", "播放中..."),
    "audio.err_mic": ("mic unavailable: {}", "麦克风不可用: {}"),
    "audio.err_save": ("save failed: {}", "保存失败: {}"),
    "audio.err_short": ("recording too short, try again", "录音太短,请重试"),
    "audio.err_play": ("playback unavailable: {}", "播放不可用: {}"),
    "audio.tone_freq": ("Frequency: {}", "频率: {}"),
    "audio.tone_amp": ("Amplitude: {:.0f} mV  ({:.1f}% FS)", "幅度: {:.0f} mV  ({:.1f}% FS)"),
    "audio.tone_readout": ("digital peak = {}   est Vdiff = {:.3f} V   vol = {}", "数字峰值 = {}   估计差分 = {:.3f} V   音量 = {}"),
    "audio.tone_clamped": ("   [CLAMPED: >FS, raise FS_MV]", "   [已削波: >FS,请调大 FS_MV]"),
    "audio.tone_fail": ("tone start failed: {}", "提示音启动失败: {}"),
    # Network
    "net.title": ("WiFi networks", "WiFi 网络"),
    "net.scan": ("scan", "扫描"),
    "net.tz": ("timezone", "时区"),
    "net.key.ip": ("ip", "IP"),
    "net.key.network": ("network", "网络"),
    "net.key.ntp": ("ntp", "NTP"),
    "net.state.idle": ("offline", "离线"),
    "net.state.connecting": ("connecting", "连接中"),
    "net.state.connected": ("connected", "已连接"),
    "net.state.stopped": ("stopped", "已停止"),
    "net.scanning": ("scanning...", "扫描中..."),
    "net.none_found": ("no networks found", "未发现网络"),
    "net.scan_timeout": ("scan timeout", "扫描超时"),
    "net.join": ("join: {}", "加入: {}"),
    "net.connect": ("connect", "连接"),
    "net.err.not_in_range": ("saved network not in range", "已保存的网络不在范围内"),
    "net.err.restore": ("scan: link restore failed", "扫描: 恢复连接失败"),
    "net.err.scan": ("scan: {}", "扫描错误: {}"),
    "net.err.ntp": ("ntp {}: {}", "NTP {}: {}"),
    "net.err.assoc": ("assoc failed (AP seen? check password/router)", "关联失败(请确认密码或路由器)"),
    # Shared keyboard overlay
    "kb.password": ("password", "密码"),
    # IMU (calibration dialogs use % formatting, kept in both halves)
    "imu.footer": ("{}Hz  {}  |  {}/{}  {}  |  OK:cal  CANCEL:exit", "{}Hz  {}  |  {}/{}  {}  |  OK:校准  CANCEL:退出"),
    "imu.swipe_left": ("swipe left", "左滑"),
    "imu.swipe_right": ("swipe right", "右滑"),
    "imu.gyro_cal": ("gyro cal", "陀螺已校"),
    "imu.gyro_raw": ("gyro RAW", "陀螺未校"),
    "imu.acc_cal": ("acc cal", "加计已校"),
    "imu.acc_raw": ("acc RAW", "加计未校"),
    "imu.menu_body": ("Gyro:   %s\nAccel:  %s\n\nGyro takes ~5 s, accel takes ~1 min.", "陀螺仪: %s\n加速度计: %s\n\n陀螺仪约 5 秒,加速度计约 1 分钟。"),
    "imu.calibrated": ("calibrated", "已校准"),
    "imu.raw_state": ("RAW", "未校准"),
    "imu.gyro_title": ("Gyro calibration", "陀螺仪校准"),
    "imu.acc_title": ("Accel calibration", "加速度计校准"),
    "imu.menu_title": ("Calibrate", "校准"),
    "imu.btn_start": ("Start", "开始"),
    "imu.btn_skip": ("Skip", "跳过"),
    "imu.btn_back": ("Back", "返回"),
    "imu.btn_gyro": ("Gyro", "陀螺仪"),
    "imu.btn_accel": ("Accel", "加速度计"),
    "imu.btn_sample": ("Sample", "采样"),
    "imu.btn_skip_all": ("Skip all", "全部跳过"),
    "imu.btn_retry": ("Retry", "重试"),
    "imu.btn_keep": ("Keep", "保留"),
    "imu.need_gyro": ("No gyro calibration data found.", "未找到陀螺仪校准数据。"),
    "imu.need_acc": ("No accelerometer calibration found.", "未找到加速度计校准数据。"),
    "imu.acc_not_yet": ("Accelerometer not calibrated yet.", "加速度计尚未校准。"),
    "imu.recal_gyro": ("Recalibrate the gyro?", "重新校准陀螺仪?"),
    "imu.recal_acc": ("Recalibrate the accelerometer?", "重新校准加速度计?"),
    "imu.gyro_prompt": ("%s\n\nPlace the board on a flat, still surface.\nTakes about 5 seconds.", "%s\n\n请将板子放在平稳静止的台面上。\n大约需要 5 秒。"),
    "imu.acc_prompt": ("%s\n\nSix faces, ~2 s each. Total about 1 minute.\nKeep the board still on each face.", "%s\n\n共六个面,每面约 2 秒,总计约 1 分钟。\n每个面都请保持静止。"),
    "imu.hold_still": ("Hold still", "保持静止"),
    "imu.countdown": ("Calibrating in %d...\n\nDo not touch the board.", "%d 秒后开始校准...\n\n请勿触碰板子。"),
    "imu.calibrating": ("Calibrating", "校准中"),
    "imu.collecting": ("Collecting samples... %d%%  (%d)\n\nDo not touch the board.", "正在采样... %d%%  (%d)\n\n请勿触碰板子。"),
    "imu.motion": ("Too much motion (sd %.1f dps).\nKeep the board still.", "晃动过大 (sd %.1f dps)。\n请保持板子静止。"),
    "imu.face": ("Face %d/6", "第 %d/6 面"),
    "imu.pose_place": ("%s\n\nPlace the board, then press OK / Sample.", "%s\n\n放好板子,然后按 OK / 采样。"),
    "imu.verify": ("Verify 7/7", "校验 7/7"),
    "imu.verify_body": ("%s\n\nChecking residual error.\nPress OK / Sample.", "%s\n\n正在检查残余误差。\n按 OK / 采样。"),
    "imu.hold_count": ("Hold still... %d", "保持静止... %d"),
    "imu.sampling": ("%s\n\nSampling... %d%%\nKeep still.", "%s\n\n采样中... %d%%\n保持静止。"),
    "imu.verify_retry": ("%s\n\nToo much motion. Press OK / Sample.", "%s\n\n晃动过大。按 OK / 采样。"),
    "imu.check_failed": ("Check failed", "检查未通过"),
    "imu.retry_keep": ("%s\n\nRetry all or keep old?", "%s\n\n全部重测还是保留旧值?"),
    "imu.residual_high": ("Residual high", "残余误差偏大"),
    "imu.residual_retry": ("Residual: %.0f mg\n\nRetry all or keep anyway?", "残余误差: %.0f mg\n\n全部重测还是强行保留?"),
    "imu.face_retry": ("%s\n\n%s\nPress OK / Sample to retry.", "%s\n\n%s\n按 OK / 采样重试。"),
    "imu.motion_plain": ("Too much motion.", "晃动过大。"),
    "imu.face_wrong": ("a face looks wrong", "某一面数据异常"),
    "imu.saved": ("Saved", "已保存"),
    "imu.saved_body": ("Accel calibrated.\n\nResidual: %.1f mg", "加速度计已校准。\n\n残余误差: %.1f mg"),
    "imu.why_missing": ("missing face %d/%d", "缺少第 %d/%d 面数据"),
    "imu.why_scale": ("axis %s scale out of range (%.4f)", "%s 轴比例超限 (%.4f)"),
    "imu.fail_samples": ("FAIL too few samples (%d < %d)", "样本不足 (%d < %d)"),
    "imu.fail_motion": ("FAIL motion (%d/%d frames over %g dps)", "晃动超限 (%d/%d 帧超过 %g dps)"),
    "imu.fail_sd": ("FAIL sd axis %s (%.4f > %.4f g)", "%s 轴抖动超限 (%.4f > %.4f g)"),
    "imu.fail_face": ("wrong face? gravity on %s not %s%s (read %+.4f g)", "面放错了? 重力在 %s 轴而非 %s%s (读数 %+.4f g)"),
    # Pose prompts: the English list doubles as the calibration log label
    "imu.pose.0": ("Face DOWN on table", "屏幕朝下放桌上"),
    "imu.pose.1": ("Face UP on table", "屏幕朝上放桌上"),
    "imu.pose.2": ("LEFT edge down", "左侧边朝下"),
    "imu.pose.3": ("RIGHT edge down", "右侧边朝下"),
    "imu.pose.4": ("TOP edge down", "顶边朝下"),
    "imu.pose.5": ("BOTTOM edge down", "底边朝下"),
    "imu.pose.6": ("Face UP on table", "屏幕朝上放桌上"),
    # en half mirrors IMU_UNAVAILABLE in apps/app_imu.py
    "common.imu_unavailable": ("IMU (QMI8658A) not detected.\nThe IMU did not respond on I2C3.\nRestart the board and try again.", "未检测到 IMU (QMI8658A)。\nI2C3 上无响应。\n请重启开发板后重试。"),
    # Settings
    "settings.section.sound": ("SOUND", "声音"),
    "settings.section.display": ("DISPLAY", "显示"),
    "settings.section.network": ("NETWORK", "网络"),
    "settings.section.system": ("SYSTEM", "系统"),
    "settings.section.reset": ("RESET", "重置"),
    "settings.click_sound": ("Button feedback", "按键反馈"),
    "settings.click_sound.hint": ("click when a button, key or tile is pressed",
                                  "按下按钮或磁贴时发声"),
    "settings.shutter_sound": ("Camera shutter", "快门声音"),
    "settings.shutter_sound.hint": ("synthesised shutter sound on every photo",
                                    "每次拍照播放合成快门音"),
    "settings.brightness": ("Brightness", "亮度"),
    "settings.brightness.hint": ("panel backlight, {}-{}%", "屏幕背光,{}-{}%"),
    "settings.language": ("Language", "语言"),
    "settings.language.hint": ("desktop and app text", "桌面与应用文字"),
    "settings.wifi_tz": ("Wi-Fi and timezone", "Wi-Fi 和时区"),
    "settings.wifi_tz.value": ("in the Network app", "在网络应用中"),
    "settings.gui_version": ("GUI version", "GUI 版本"),
    "settings.firmware": ("Firmware", "固件"),
    "settings.machine": ("Machine", "机型"),
    "settings.heap": ("Python heap", "Python 堆"),
    "settings.heap.hint": ("GC arena, grows on demand; not DDR",
                           "GC 堆,按需增长,非 DDR"),
    "settings.heap.value": ("{:.2f} MB free", "剩余 {:.2f} MB"),
    "settings.models": ("KPU models", "KPU 模型"),
    "settings.models.value": ("{} resident", "{} 个常驻"),
    "settings.models.na": ("n/a", "不可用"),
    "settings.file": ("Settings file", "设置文件"),
    "settings.reset": ("Restore defaults", "恢复默认"),
    "settings.reset.hint": ("tap twice to confirm", "点两次确认"),
    "settings.action.run": ("tap to run", "点击执行"),
    "settings.action.confirm": ("tap again to confirm", "再点一次确认"),
    "settings.action.done": ("defaults restored", "已恢复默认"),
    # Plate: the short form the status bar uses ("TAP to freeze" does not fit
    # the bar next to the count).
    "ai.tap_freeze_short": ("TAP freeze", "点击冻结"),
    # Face recognition
    "rec.title": ("Recognize {}", "识别 {}"),
    "rec.title_plain": ("Recognize", "识别"),
    "rec.unknown": ("unknown", "未知"),
    "rec.unknown_score": ("unknown {:.2f}", "未知 {:.2f}"),
    "rec.ok_add": ("OK: add {}/{}", "OK: 添加 {}/{}"),
    "rec.ok_enroll": ("OK: enroll", "OK: 录入"),
    "rec.photo": ("PHOTO", "照片"),
    "rec.no_enrolled_title": ("No face enrolled", "尚未录入人脸"),
    "rec.no_enrolled_body": ("Put one face in view, then press OK\n"
                             "and type a name to enroll it.",
                             "让一张人脸进入画面,然后按 OK\n输入名字完成录入。"),
    "rec.no_enrolled_hint": ("OK: enroll      tap here: close",
                             "OK 录入      点此关闭"),
    "rec.enrolled_title": ("Enrolled faces ({})", "已录入人脸 ({})"),
    "rec.manage_hint": ("tap a row to delete, tap outside to close",
                        "点行删除,点外侧关闭"),
    "rec.voice_row": ("VOICE  say the name", "语音  播报名字"),
    "rec.voice_missing": ("make {} missing voice(s)", "生成 {} 个缺失语音"),
    "rec.empty_db": ("none yet - press OK to enroll", "暂无记录,按 OK 录入"),
    "rec.row": ("{}{} x{}    (tap to delete)", "{}{} x{}    (点按删除)"),
    "rec.tag_photo": (" [photo]", " [照片]"),
    "rec.kb_title": ("name this face", "给这张人脸命名"),
    "rec.kb_save": ("save", "保存"),
    # Recognition status notes (2.5 s in the name field)
    "rec.note_photos_allowed": ("photos allowed", "照片已允许"),
    "rec.note_photos_excluded": ("photos excluded", "照片已排除"),
    "rec.note_voice_on": ("voice on", "语音开"),
    "rec.note_voice_off": ("voice off", "语音关"),
    "rec.note_no_face_enroll": ("no face to enroll", "没有可录入的人脸"),
    "rec.note_db_full": ("database full", "数据库已满"),
    "rec.note_photo_blocked": ("photo blocked: enable PHOTO",
                               "照片已阻止:请开启 PHOTO"),
    "rec.note_photo_face": ("photo face detected", "检测到照片人脸"),
    "rec.note_empty_name": ("empty name, not saved", "名字为空,未保存"),
    "rec.note_feature_lost": ("feature lost, retry", "特征丢失,请重试"),
    "rec.note_save_failed": ("save failed", "保存失败"),
    "rec.note_delete_failed": ("delete failed", "删除失败"),
    "rec.note_enroll_turn": ("{} {}/{} - turn head", "{} {}/{} - 转动头部"),
    "rec.note_enroll_turn_short": ("{} {}/{} - turn", "{} {}/{} - 转动"),
    "rec.note_no_face_named": ("no face - {}", "无人脸 - {}"),
    "rec.note_enroll_done": ("{} {}/{} done", "{} {}/{} 完成"),
    "rec.note_keep": ("{} {}/{} keep", "{} {}/{} 保留"),
    "rec.note_voice_start": ("voice 0/{}", "语音 0/{}"),
    "rec.note_voice_progress": ("voice {}/{}", "语音 {}/{}"),
    "rec.note_voices_ready": ("voices ready {}/{}", "语音完成 {}/{}"),
}

_lang = "en"
_home_dirty = False


def init():
    """Read the stored language after config.load(). Unknown values fall back to en."""
    global _lang
    value = config.get("ui.language", "en")
    _lang = value if value in LANGS else "en"
    return _lang


def lang():
    return _lang


def set_lang(code):
    """Switch language and persist. Returns True when the value actually changed."""
    global _lang, _home_dirty
    if code not in LANGS or code == _lang:
        return False
    _lang = code
    config.set("ui.language", code)
    _home_dirty = True
    return True


def mark_home_dirty():
    """Force a home rebuild without changing the language (restore defaults)."""
    global _home_dirty
    _home_dirty = True


def consume_home_dirty():
    global _home_dirty
    if _home_dirty:
        _home_dirty = False
        return True
    return False


def name(entry_id, fallback=""):
    pair = NAMES.get(entry_id)
    if pair is None:
        return fallback
    return pair[1] if _lang == "zh" else pair[0]


def needs_cjk(text):
    """True when text has a non-ASCII code point: the Latin faces have no CJK glyphs."""
    for ch in text:
        if ord(ch) > 127:
            return True
    return False


def t(key, fallback=""):
    """Look up an app string for the current language; fallback (or the key) when missing."""
    pair = STRINGS.get(key)
    if pair is None:
        return fallback or key
    return pair[1] if _lang == "zh" else pair[0]


def t_en(en_text):
    """Look up a STRINGS entry by its English half; en_text itself when missing.
    For tables the apps already own (threshold names, view names)."""
    for pair in STRINGS.values():
        if pair[0] == en_text:
            return pair[1] if _lang == "zh" else pair[0]
    return en_text


def font_for(text, latin, cjk=CJK_FONT_16):
    """Font that can render text: the baked UI CJK face for non-ASCII, else latin.
    font_pool imports lvgl, so it is imported here lazily (core rule 2)."""
    if not needs_cjk(text):
        return latin
    try:
        from core import font_pool
        return font_pool.get(cjk) or latin
    except BaseException:
        return latin


# AppUnavailable payloads are English constants in the raising module (they are
# compared by value, e.g. errors.CAMERA_UNAVAILABLE). This maps one onto its
# STRINGS key so the launcher panel can translate at display time.
_ERROR_KEYS = ("common.camera_unavailable", "common.battery_unavailable",
               "common.imu_unavailable", "common.seg_buf_unavailable")


def translate_error(message):
    """Localized form of a known AppUnavailable payload, else the message itself."""
    for key in _ERROR_KEYS:
        pair = STRINGS.get(key)
        if pair is not None and message == pair[0]:
            return t(key)
    return message
