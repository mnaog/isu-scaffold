//go:build go1.25

// flight recorder（runtime/traceの直前の記録）。Go 1.25から。profile.goの説明を参照。
package main

import (
	"errors"
	"io"
	"runtime/trace"
	"time"
)

var profileFlight *trace.FlightRecorder

func profileFlightStart(age time.Duration, maxBytes uint64) error {
	recorder := trace.NewFlightRecorder(trace.FlightRecorderConfig{MinAge: age, MaxBytes: maxBytes})
	if err := recorder.Start(); err != nil {
		return err
	}
	profileFlight = recorder
	return nil
}

func profileFlightWrite(w io.Writer) error {
	if profileFlight == nil || !profileFlight.Enabled() {
		return errors.New("the flight recorder is off; set ISUCON_FLIGHT_RECORDER to the seconds to keep")
	}
	_, err := profileFlight.WriteTo(w)
	return err
}
