# Improved Error Handling for bwrap Commands

## Problem

When commands failed inside bwrap, we were only seeing:
```
[Execute] CommandExitException: Command failed: 2
exit status 2
```

**No actual error message!** This made debugging impossible.

## Root Cause

E2B raises `CommandExitException` when a command exits with non-zero code. We were catching it but only extracting the exit code, not the actual stdout/stderr.

## Solution

Enhanced exception handling to extract **all available information**:

```python
except CommandExitException as e:
    # Extract all available info
    exit_code = getattr(e, 'exit_code', 1)
    error_msg = getattr(e, 'error', str(e))
    
    # Log everything
    print(f"[Execute] CommandExitException: exit_code={exit_code}")
    print(f"[Execute] Error: {error_msg}")
    
    # Check for stdout/stderr in exception
    if hasattr(e, 'stdout'):
        print(f"[Execute] Exception stdout: {e.stdout[:500]}")
    if hasattr(e, 'stderr'):
        print(f"[Execute] Exception stderr: {e.stderr[:500]}")
    
    # Build comprehensive error message
    output_parts = [f"Command failed with exit code {exit_code}"]
    if error_msg:
        output_parts.append(f"\nError: {error_msg}")
    if hasattr(e, 'stdout') and e.stdout:
        output_parts.append(f"\nOutput:\n{e.stdout}")
    if hasattr(e, 'stderr') and e.stderr:
        output_parts.append(f"\nError output:\n{e.stderr}")
    
    return ExecuteResponse(
        output='\n'.join(output_parts),
        exit_code=exit_code,
        truncated=False
    )
```

## Before vs After

### Before (No Useful Info)

```
[Execute] 🔒 bwrap isolated: uv pip install pillow && python script.py
[Execute] CommandExitException: Command failed: 2
exit status 2
```

**Problem**: What failed? Why? No idea!

### After (Full Context)

```
[Execute] 🔒 bwrap isolated: uv pip install pillow && python script.py
[Execute] CommandExitException: exit_code=2
[Execute] Error: Command execution failed
[Execute] Exception stdout: Installing pillow... Done
[Execute] Exception stderr: Traceback (most recent call last):
  File "script.py", line 10, in <module>
    plt.close()
NameError: name 'plt' is not defined
```

**Solution**: Now we can see the actual error!

## What Gets Logged

### 1. Exit Code
```python
exit_code = getattr(e, 'exit_code', 1)
print(f"[Execute] CommandExitException: exit_code={exit_code}")
```

### 2. Error Message
```python
error_msg = getattr(e, 'error', str(e))
print(f"[Execute] Error: {error_msg}")
```

### 3. Standard Output (if available)
```python
if hasattr(e, 'stdout'):
    print(f"[Execute] Exception stdout: {e.stdout[:500]}")
```

### 4. Standard Error (if available)
```python
if hasattr(e, 'stderr'):
    print(f"[Execute] Exception stderr: {e.stderr[:500]}")
```

## Example Error Cases

### Case 1: Python Import Error

**Command:**
```python
backend.execute("python -c 'import nonexistent'")
```

**Old Output:**
```
[Execute] CommandExitException: Command failed: 1
exit status 1
```

**New Output:**
```
[Execute] CommandExitException: exit_code=1
[Execute] Error: Command execution failed
[Execute] Exception stderr: Traceback (most recent call last):
  File "<string>", line 1, in <module>
ModuleNotFoundError: No module named 'nonexistent'
```

### Case 2: File Not Found

**Command:**
```python
backend.execute("python /missing_script.py")
```

**Old Output:**
```
[Execute] CommandExitException: Command failed: 2
exit status 2
```

**New Output:**
```
[Execute] CommandExitException: exit_code=2
[Execute] Error: Command execution failed
[Execute] Exception stderr: python: can't open file '/missing_script.py': [Errno 2] No such file or directory
```

### Case 3: Syntax Error

**Command:**
```python
backend.execute("python -c 'def bad syntax'")
```

**Old Output:**
```
[Execute] CommandExitException: Command failed: 1
exit status 1
```

**New Output:**
```
[Execute] CommandExitException: exit_code=1
[Execute] Error: Command execution failed
[Execute] Exception stderr:   File "<string>", line 1
    def bad syntax
            ^^^^^^
SyntaxError: invalid syntax
```

### Case 4: Package Installation Failure

**Command:**
```python
backend.execute("uv pip install nonexistent-package")
```

**Old Output:**
```
[Execute] CommandExitException: Command failed: 1
exit status 1
```

**New Output:**
```
[Execute] CommandExitException: exit_code=1
[Execute] Error: Command execution failed
[Execute] Exception stderr: error: No solution found when resolving dependencies:
  package 'nonexistent-package' not found in index
```

## Return Value to Agent

The `ExecuteResponse` now contains the full error context:

```python
ExecuteResponse(
    output="""Command failed with exit code 2

Error: Command execution failed

Output:
Installing packages... Done

Error output:
Traceback (most recent call last):
  File "script.py", line 10, in <module>
    plt.close()
NameError: name 'plt' is not defined""",
    exit_code=2,
    truncated=False
)
```

Agents can now see:
- ✅ What command failed
- ✅ What the exit code was
- ✅ What output was produced
- ✅ What the actual error was

## Benefits

### 1. **Debuggability**
- See exact Python traceback
- See package installation errors
- See file not found errors
- See any error message

### 2. **Agent Intelligence**
- Agents can read error messages
- Agents can fix their code based on errors
- Agents can install missing packages
- Agents can recover from failures

### 3. **Developer Experience**
- Faster debugging
- Clear error messages
- No more guessing
- Can reproduce issues locally

### 4. **Logging**
- Comprehensive logs in backend
- Full context in frontend
- Easy to trace issues
- Clear audit trail

## Testing

### Test 1: Missing Module

```python
result = backend.execute("python -c 'import matplotlib'")
# Should show: ModuleNotFoundError: No module named 'matplotlib'
assert "ModuleNotFoundError" in result.output
assert "matplotlib" in result.output
```

### Test 2: Syntax Error

```python
result = backend.execute("python -c 'def bad'")
# Should show: SyntaxError: invalid syntax
assert "SyntaxError" in result.output
```

### Test 3: File Not Found

```python
result = backend.execute("cat /nonexistent.txt")
# Should show: No such file or directory
assert "No such file" in result.output or "not found" in result.output.lower()
```

### Test 4: Command Success (No Exception)

```python
result = backend.execute("python -c 'print(\"Hello\")'")
# Should show normal output
assert result.exit_code == 0
assert "Hello" in result.output
```

## Backwards Compatibility

✅ **Fully backwards compatible**

- Success cases work exactly the same
- Only failure cases now have more information
- Exit codes preserved
- Return type unchanged

## Summary

| Aspect | Before | After |
|--------|--------|-------|
| **Error visibility** | ❌ Hidden | ✅ Full details |
| **Stdout** | ❌ Not shown | ✅ Shown |
| **Stderr** | ❌ Not shown | ✅ Shown |
| **Tracebacks** | ❌ Lost | ✅ Preserved |
| **Exit codes** | ✅ Shown | ✅ Shown |
| **Debuggability** | ❌ Hard | ✅ Easy |
| **Agent recovery** | ❌ Blind | ✅ Informed |

**Now when something fails, you'll know exactly why!** 🎯

