[app]
title = MFS IDE
package.name = mfside
package.domain = org.mfslang
source.dir = .
source.include_exts = py,mfs,mfslib
version = 1.0

requirements = python3,kivy==2.3.0

orientation = portrait
fullscreen = 0

android.permissions = INTERNET
android.api = 33
android.minapi = 21
android.ndk = 25b
android.sdk = 33
android.archs = arm64-v8a, armeabi-v7a

android.allow_backup = True

[buildozer]
log_level = 2
warn_on_root = 1
