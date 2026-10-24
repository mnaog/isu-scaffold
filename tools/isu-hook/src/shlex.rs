//! Python's `shlex` in POSIX mode with `whitespace_split`, no comments, and optional punctuation
//! characters: the tokens the guard has always split shell commands into, quirks included.

#[derive(Debug, PartialEq)]
pub enum LexError {
    NoClosingQuotation,
    NoEscapedCharacter,
}

#[derive(Clone, Copy, PartialEq)]
enum State {
    Whitespace,
    Word,
    Punctuation,
    Quote(char),
    Escape(char),
}

/// Splits `text` like `shlex.shlex(text, posix=True, punctuation_chars=punctuation)` with the given
/// whitespace, `whitespace_split = True` and `commenters = ''`. Runs of punctuation characters are
/// tokens of their own; an empty quoted word is an empty token.
pub fn split(text: &str, whitespace: &str, punctuation: &str) -> Result<Vec<String>, LexError> {
    let chars: Vec<char> = text.chars().collect();
    let mut position = 0;
    let mut pushback: Option<char> = None;
    let mut tokens = Vec::new();
    let mut state = State::Whitespace;
    loop {
        let mut token = String::new();
        let mut quoted = false;
        let mut escaped_state = State::Word;
        let mut ended = false;
        loop {
            let next = match pushback.take() {
                Some(c) => Some(c),
                None if position < chars.len() => {
                    position += 1;
                    Some(chars[position - 1])
                }
                None => None,
            };
            match state {
                State::Whitespace => match next {
                    None => {
                        ended = true;
                        break;
                    }
                    Some(c) if whitespace.contains(c) => {
                        if !token.is_empty() || quoted {
                            break;
                        }
                    }
                    Some('\\') => {
                        escaped_state = State::Word;
                        state = State::Escape('\\');
                    }
                    Some(c) if punctuation.contains(c) => {
                        token.push(c);
                        state = State::Punctuation;
                    }
                    Some(c) if c == '\'' || c == '"' => state = State::Quote(c),
                    Some(c) => {
                        token.push(c);
                        state = State::Word;
                    }
                },
                State::Quote(quote) => {
                    quoted = true;
                    match next {
                        None => return Err(LexError::NoClosingQuotation),
                        Some(c) if c == quote => state = State::Word,
                        Some('\\') if quote == '"' => {
                            escaped_state = state;
                            state = State::Escape('\\');
                        }
                        Some(c) => token.push(c),
                    }
                }
                State::Escape(escape) => {
                    let Some(c) = next else { return Err(LexError::NoEscapedCharacter) };
                    // Inside quotes only the quote itself or the escape character may be escaped.
                    if let State::Quote(quote) = escaped_state {
                        if c != escape && c != quote {
                            token.push(escape);
                        }
                    }
                    token.push(c);
                    state = escaped_state;
                }
                State::Word | State::Punctuation => match next {
                    None => {
                        ended = true;
                        break;
                    }
                    Some(c) if whitespace.contains(c) => {
                        state = State::Whitespace;
                        if !token.is_empty() || quoted {
                            break;
                        }
                    }
                    Some(c) if state == State::Punctuation => {
                        if punctuation.contains(c) {
                            token.push(c);
                        } else {
                            pushback = Some(c);
                            state = State::Whitespace;
                            break;
                        }
                    }
                    Some(c) if c == '\'' || c == '"' => state = State::Quote(c),
                    Some('\\') => {
                        escaped_state = State::Word;
                        state = State::Escape('\\');
                    }
                    Some(c) if !punctuation.contains(c) => token.push(c),
                    Some(c) => {
                        pushback = Some(c);
                        state = State::Whitespace;
                        if !token.is_empty() || quoted {
                            break;
                        }
                    }
                },
            }
        }
        if ended && token.is_empty() && !quoted {
            return Ok(tokens);
        }
        tokens.push(token);
        if ended {
            return Ok(tokens);
        }
    }
}

/// `shlex.quote`: a word the shell reads back as itself.
pub fn quote(word: &str) -> String {
    if word.is_empty() {
        return "''".to_string();
    }
    if word.chars().all(|c| c.is_ascii_alphanumeric() || "_@%+=:,./-".contains(c)) {
        return word.to_string();
    }
    format!("'{}'", word.replace('\'', "'\"'\"'"))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn shell(text: &str) -> Vec<String> {
        split(text, " \t\r", ";&|()\n").unwrap()
    }

    #[test]
    fn splits_like_python() {
        assert_eq!(shell("a && b|c;d"), ["a", "&&", "b", "|", "c", ";", "d"]);
        assert_eq!(shell("echo 'a b' \"c\\\"d\" e\\ f"), ["echo", "a b", "c\"d", "e f"]);
        assert_eq!(shell("x ''"), ["x", ""]);
        assert_eq!(shell("a\nb"), ["a", "\n", "b"]);
        assert_eq!(shell("\"a\\nb\""), ["a\\nb"]);
        assert_eq!(split("'a", " ", ""), Err(LexError::NoClosingQuotation));
        assert_eq!(split("a\\", " ", ""), Err(LexError::NoEscapedCharacter));
        assert_eq!(quote("it's"), "'it'\"'\"'s'");
    }
}
