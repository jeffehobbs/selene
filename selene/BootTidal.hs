:set -fno-warn-orphans -Wno-type-defaults -XMultiParamTypeClasses -XOverloadedStrings
:set prompt ""
:set prompt-cont ""

import Sound.Tidal.Boot
import qualified System.IO as IO
import qualified System.Environment as Env

-- Tidal's threads print while GHCi does; unbuffered output interleaves
-- them character by character.
IO.hSetBuffering IO.stdout IO.LineBuffering

default (Rational, Integer, Double, Pattern String)

-- selene picks a free control port so Flow's /ctrl messages can't land on
-- another Tidal instance holding the default 6010.
seleneCtrlPort <- maybe 6010 read <$> Env.lookupEnv "SELENE_CTRL_PORT" :: IO Int

-- A copy of every event goes to selene's tap so Flow knows where the cycle
-- is and can land changes exactly on the beat.
seleneTapPort <- maybe 0 read <$> Env.lookupEnv "SELENE_TAP_PORT" :: IO Int
seleneTap = [(superdirtTarget {oName = "selene", oPort = seleneTapPort, oBusPort = Nothing, oHandshake = False}, [superdirtShape]) | seleneTapPort > 0]

tidalInst <- mkTidalWith ((superdirtTarget, [superdirtShape]) : seleneTap) (defaultConfig {cCtrlPort = seleneCtrlPort})

instance Tidally where tidal = tidalInst
