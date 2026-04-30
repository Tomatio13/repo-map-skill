(program_name) @name.definition.module @definition.module

(section_header) @name.definition.module @definition.module

(paragraph_header) @name.definition.function @definition.function

(constant_entry
  name: (WORD) @name.definition.constant) @definition.constant

(data_description
  (entry_name) @name.definition.variable) @definition.variable

(file_description_entry
  (WORD) @name.definition.variable) @definition.variable

(call_statement
  x: [
    (qualified_word) @name.reference.call
    (string) @name.reference.call
    (h_string) @name.reference.call
    (n_string) @name.reference.call
    (x_string) @name.reference.call
  ]) @reference.call

(copy_statement
  book: [
    (WORD) @name.reference.call
    (string) @name.reference.call
  ]) @reference.call

(perform_procedure
  (label) @name.reference.call) @reference.call
