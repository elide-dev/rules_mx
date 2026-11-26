define i32 @answer() {
  ret i32 42
}

define i32 @main() {
  %result = call i32 @answer()
  ret i32 %result
}
