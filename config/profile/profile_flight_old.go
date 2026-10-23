//go:build !go1.25

// Go 1.25より前にはflight recorderが無い。profile.goの説明を参照。
package main

import (
	"errors"
	"io"
	"time"
)

func profileFlightStart(time.Duration, uint64) error {
	return errors.New("this Go has no flight recorder (Go 1.25 or later)")
}

func profileFlightWrite(io.Writer) error {
	return errors.New("this Go has no flight recorder (Go 1.25 or later)")
}
