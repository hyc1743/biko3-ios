#import "Biko3ImportViewController.h"
#import "Biko3ResourceInstaller.h"
#include <stdio.h>

@implementation Biko3ImportViewController {
    UILabel *_status;
    UIProgressView *_progress;
    UIButton *_retry;
    BOOL _running;
    BOOL _finished;
    BOOL (^_ready)(void);
}
- (instancetype)initWithReady:(BOOL (^)(void))ready {
    if ((self = [super init])) _ready = [ready copy];
    return self;
}
- (void)viewDidLoad {
    [super viewDidLoad];
    self.view.backgroundColor = UIColor.systemBackgroundColor;
    UILabel *title = [UILabel new];
    title.text = @"Biko3 资源准备";
    title.font = [UIFont boldSystemFontOfSize:26];
    title.textAlignment = NSTextAlignmentCenter;
    UILabel *help = [UILabel new];
    help.text = @"将 6 个 Biko3Data 分卷复制到“文件 → 我的 iPhone → Biko3”，或其中的 ResourcePacks 文件夹。\n自动校验并解包后进入游戏。请保持应用在前台，并为解包预留约 3 GB 空间。";
    help.numberOfLines = 0;
    help.textAlignment = NSTextAlignmentCenter;
    _status = [UILabel new];
    _status.numberOfLines = 0;
    _status.textAlignment = NSTextAlignmentCenter;
    _progress = [UIProgressView new];
    _retry = [UIButton buttonWithType:UIButtonTypeSystem];
    [_retry setTitle:@"重新检查 / 继续解包" forState:UIControlStateNormal];
    [_retry addTarget:self action:@selector(startImport) forControlEvents:UIControlEventTouchUpInside];
    UIStackView *stack = [[UIStackView alloc] initWithArrangedSubviews:@[title, help, _progress, _status, _retry]];
    stack.axis = UILayoutConstraintAxisVertical;
    stack.spacing = 18;
    stack.translatesAutoresizingMaskIntoConstraints = NO;
    [self.view addSubview:stack];
    NSLayoutConstraint *preferredWidth = [stack.widthAnchor constraintEqualToAnchor:self.view.safeAreaLayoutGuide.widthAnchor multiplier:0.88];
    preferredWidth.priority = UILayoutPriorityDefaultHigh;
    [NSLayoutConstraint activateConstraints:@[
        [stack.centerXAnchor constraintEqualToAnchor:self.view.safeAreaLayoutGuide.centerXAnchor],
        [stack.centerYAnchor constraintEqualToAnchor:self.view.safeAreaLayoutGuide.centerYAnchor],
        [stack.widthAnchor constraintLessThanOrEqualToConstant:680],
        [stack.leadingAnchor constraintGreaterThanOrEqualToAnchor:self.view.safeAreaLayoutGuide.leadingAnchor constant:24],
        [stack.trailingAnchor constraintLessThanOrEqualToAnchor:self.view.safeAreaLayoutGuide.trailingAnchor constant:-24],
        preferredWidth
    ]];
    [NSNotificationCenter.defaultCenter addObserver:self selector:@selector(startImport)
        name:UISceneDidActivateNotification object:nil];
}
- (void)viewDidAppear:(BOOL)animated {
    [super viewDidAppear:animated];
    [self startImport];
}
- (void)dealloc { [NSNotificationCenter.defaultCenter removeObserver:self]; }
- (void)finishIfActive {
    UIWindowScene *scene = self.view.window.windowScene;
    if (!_finished || !_ready || !scene || scene.activationState != UISceneActivationStateForegroundActive) return;
    if (_ready()) _ready = nil;
    else {
        _finished = NO;
        _status.text = @"启动时未找到资源，请检查 game/Data 后重试。";
    }
}
- (void)startImport {
    [self finishIfActive];
    if (_running || _finished || !self.view.window ||
        self.view.window.windowScene.activationState != UISceneActivationStateForegroundActive) return;
    _running = YES;
    _retry.enabled = NO;
    _status.text = @"正在检查资源…";
    UIApplication.sharedApplication.idleTimerDisabled = YES;
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
        @autoreleasepool {
            NSError *error = nil;
            NSURL *manifest = [NSBundle.mainBundle.resourceURL URLByAppendingPathComponent:@"dm/Biko3Data.manifest.json"];
            NSURL *documents = [NSFileManager.defaultManager URLsForDirectory:NSDocumentDirectory inDomains:NSUserDomainMask].firstObject;
            Biko3ResourceInstaller *installer = [[Biko3ResourceInstaller alloc] initWithManifestURL:manifest documentsURL:documents error:&error];
            BOOL ok = installer && [installer installWithProgress:^(NSString *phase, double fraction) {
                dispatch_async(dispatch_get_main_queue(), ^{
                    self->_status.text = phase;
                    self->_progress.progress = (float)fraction;
                });
            } error:&error];
            fprintf(stderr, "resources: %s\n", ok ? "ready" : error.localizedDescription.UTF8String);
            dispatch_async(dispatch_get_main_queue(), ^{
                self->_running = NO;
                self->_finished = ok;
                self->_retry.enabled = YES;
                UIApplication.sharedApplication.idleTimerDisabled = NO;
                if (ok) {
                    self->_status.text = @"资源就绪，正在启动…";
                    self->_progress.progress = 1;
                    [self finishIfActive];
                } else {
                    self->_status.text = error.localizedDescription ?: @"资源检查失败，请补齐分卷后重试。";
                }
            });
        }
    });
}
@end
