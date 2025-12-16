#!/usr/bin/env python3
"""
测试RocketMQ客户端是否正常工作
"""
import os
import sys

# 设置库路径（Mac系统标准目录）
# 优先检查系统标准目录，不需要设置DYLD_LIBRARY_PATH
import subprocess

def get_brew_prefix():
    """获取Homebrew前缀"""
    try:
        result = subprocess.run(['brew', '--prefix'], capture_output=True, text=True, timeout=2)
        if result.returncode == 0:
            return result.stdout.strip()
    except:
        pass
    return None

# 尝试多个可能的路径（系统标准目录优先）
possible_lib_paths = [
    "/opt/homebrew/lib",  # Homebrew ARM64 Mac
    "/usr/local/lib",     # 系统标准目录
    "/usr/local/homebrew/lib",  # Homebrew Intel Mac
]

# 添加Homebrew动态检测的路径
brew_prefix = get_brew_prefix()
if brew_prefix:
    brew_lib = os.path.join(brew_prefix, "lib")
    if brew_lib not in possible_lib_paths:
        possible_lib_paths.insert(0, brew_lib)

# 添加用户目录（向后兼容）
current_user = os.environ.get("SUDO_USER") or os.environ.get("USER") or "gaoyong"
possible_lib_paths.extend([
    f"/Users/{current_user}/lib",
    os.path.expanduser("~/lib"),
])

lib_path = None
lib_file = None
for path in possible_lib_paths:
    test_file = os.path.join(path, "librocketmq.dylib")
    if os.path.exists(test_file):
        lib_path = path
        lib_file = test_file
        break

if lib_file:
    print(f"📚 找到库文件: {lib_file}")
    # macOS系统目录通常不需要设置DYLD_LIBRARY_PATH
    # 但如果不在系统目录，可能需要设置
    if lib_path not in ["/opt/homebrew/lib", "/usr/local/lib", "/usr/local/homebrew/lib"]:
        if lib_path not in os.environ.get("DYLD_LIBRARY_PATH", ""):
            os.environ["DYLD_LIBRARY_PATH"] = f"{lib_path}:{os.environ.get('DYLD_LIBRARY_PATH', '')}"
            print(f"📚 设置库路径: {lib_path}")
else:
    print("⚠️  警告: 未找到librocketmq.dylib库文件")
    print("   检查的标准目录:")
    for path in possible_lib_paths[:3]:  # 只显示系统目录
        exists = "✓" if os.path.exists(os.path.join(path, "librocketmq.dylib")) else "✗"
        print(f"     {exists} {path}")

def test_import():
    """测试导入RocketMQ客户端"""
    try:
        print("🔍 测试导入rocketmq.client...")
        from rocketmq.client import Producer, PushConsumer, Message, ConsumeStatus
        print("✅ RocketMQ客户端导入成功！")
        return True
    except ImportError as e:
        print(f"❌ 导入失败: {e}")
        print("\n可能的原因：")
        print("1. rocketmq-client-python未安装: pip install rocketmq-client-python")
        print("2. librocketmq.dylib未找到，检查 ~/lib/librocketmq.dylib")
        print("3. DYLD_LIBRARY_PATH未设置")
        return False
    except Exception as e:
        print(f"❌ 其他错误: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_library_path():
    """测试库文件路径"""
    # 尝试多个可能的路径（系统标准目录优先）
    import subprocess
    
    def get_brew_prefix():
        try:
            result = subprocess.run(['brew', '--prefix'], capture_output=True, text=True, timeout=2)
            if result.returncode == 0:
                return result.stdout.strip()
        except:
            pass
        return None
    
    possible_paths = [
        "/opt/homebrew/lib/librocketmq.dylib",  # Homebrew ARM64 Mac
        "/usr/local/lib/librocketmq.dylib",     # 系统标准目录
        "/usr/local/homebrew/lib/librocketmq.dylib",  # Homebrew Intel Mac
    ]
    
    # 添加Homebrew动态检测的路径
    brew_prefix = get_brew_prefix()
    if brew_prefix:
        brew_lib = os.path.join(brew_prefix, "lib", "librocketmq.dylib")
        if brew_lib not in possible_paths:
            possible_paths.insert(0, brew_lib)
    
    # 添加用户目录（向后兼容）
    current_user = os.environ.get("SUDO_USER") or os.environ.get("USER") or "gaoyong"
    possible_paths.extend([
        f"/Users/{current_user}/lib/librocketmq.dylib",
        os.path.expanduser("~/lib/librocketmq.dylib"),
    ])
    
    # 去重
    possible_paths = list(dict.fromkeys(possible_paths))
    
    lib_file = None
    for path in possible_paths:
        if os.path.exists(path):
            lib_file = path
            break
    
    if lib_file:
        print(f"✅ 库文件存在: {lib_file}")
        # 检查文件大小
        size = os.path.getsize(lib_file)
        print(f"   文件大小: {size / 1024 / 1024:.2f} MB")
        # 检查架构
        try:
            result = subprocess.run(['file', lib_file], capture_output=True, text=True)
            arch_info = result.stdout.strip()
            print(f"   架构信息: {arch_info}")
            # 检查是否是ARM64
            if "arm64" in arch_info.lower():
                print("   ✅ 架构正确: ARM64")
            elif "x86_64" in arch_info.lower():
                print("   ⚠️  警告: 架构是x86_64，不是ARM64")
        except:
            pass
        return lib_file
    else:
        print(f"❌ 库文件不存在")
        print("   检查的标准目录:")
        for path in possible_paths[:4]:  # 显示前4个（系统目录）
            exists = "✓" if os.path.exists(path) else "✗"
            print(f"     {exists} {path}")
        print("\n   请运行: ./install_rocketmq.sh")
        return None

def main():
    """主函数"""
    print("=" * 50)
    print("RocketMQ客户端测试")
    print("=" * 50)
    print()
    
    # 测试库文件
    print("1. 检查库文件...")
    lib_file = test_library_path()
    lib_ok = lib_file is not None
    print()
    
    # 如果找到库文件，更新环境变量
    if lib_file:
        lib_dir = os.path.dirname(lib_file)
        if lib_dir not in os.environ.get("DYLD_LIBRARY_PATH", ""):
            os.environ["DYLD_LIBRARY_PATH"] = f"{lib_dir}:{os.environ.get('DYLD_LIBRARY_PATH', '')}"
    
    # 测试导入
    print("2. 测试Python导入...")
    import_ok = test_import()
    print()
    
    # 总结
    print("=" * 50)
    if lib_ok and import_ok:
        print("✅ 所有测试通过！RocketMQ客户端已就绪")
        print()
        print("下一步：")
        print("1. 配置.env文件（复制.env.example到.env）")
        print("2. 启动RocketMQ服务器（如果还没有）")
        print("3. 运行服务: python -m app.main")
        return 0
    else:
        print("❌ 测试失败，请检查上述错误")
        return 1

if __name__ == "__main__":
    sys.exit(main())

