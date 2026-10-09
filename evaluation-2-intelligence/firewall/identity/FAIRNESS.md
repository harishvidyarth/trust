# Fairness notes for the identity check

## What this check is

A short optional check that a candidate can do on their own application. The candidate follows three face prompts in the browser and reads one sentence aloud. A person on the hiring team can read a short note about it. The note is advice only. It never changes a score, a route or a result, and it never rejects anyone. Candidates are only told that their check was received.

## What is measured

Face check
- Whether the candidate's browser saw a face in enough camera frames.
- Whether the three prompts were done in the order they were issued and in a believable time.
- These numbers are measured inside the candidate's own browser and sent to the server. They can be forged.

Voice check
- How much the loudness changes over the recording.
- How much the pitch wobbles from one voice cycle to the next.
- How much the sound colour moves and how much the sound repeats itself.
- Whether the recording starts abruptly.
- Whether the spoken digits match the code in the issued sentence.
- All of this is a hand tuned rule set called heuristic-v1. It is not a trained model.

## What is not measured

- Accent, language skill, age, gender, skin tone, looks or any other feature of appearance.
- Who the person is. The optional photo match described below only says how alike two faces look. It does not say who anyone is.
- Anything that could be used to look the person up.
- How well the candidate speaks, how loud they are or how high or low their voice is. The rules use relative changes inside one recording, not absolute levels.

## What is kept

- No video, no pictures and no audio are stored. They are checked in memory and dropped.
- Only a few numbers and a short plain note are kept, for 90 days.
- A missing part is never held against a candidate.

## Known limits

- The voice rules were tuned by hand on a small set of examples and have not been tested on a large and varied group of speakers.
- Quiet rooms, cheap microphones, headsets, noise filters and phone calls can all change the numbers. A real person can look unusual and a clever fake can look normal.
- Good speech cloning will likely pass. Played back audio of a real person can pass.
- The face prompts depend on camera quality, lighting, glasses, head coverings, facial differences and disabilities. Some people cannot do some prompts. This must never count against them.
- The spoken code is checked with speech to text. If the server has no speech model the browser text is used and anyone can fake it. Speech to text is less accurate for some accents and speech patterns, so a failed code match is a reason to ask for a live check and not a reason to doubt the person.
- Anyone who can send requests can send made up numbers for the face part.

## Testing needed before any weight is considered

1. Collect consented recordings from a large and varied group of speakers across accents, ages, genders, microphones, rooms and speech differences. Include people with disabilities.
2. Measure how often real people are flagged, by group. Compare the rates between groups. Fix or drop any rule where one group is flagged much more often than another.
3. Test against current speech cloning and playback attacks and report how many get through.
4. Test the face prompts with different cameras, lighting, glasses, head coverings and facial differences, and offer a live check as an equal alternative.
5. Have an outside reviewer look at the results and the wording.
6. Until all of this is done the check stays advice only with no weight in any decision.


## A measured limit, written down on purpose

On 9 October 2026 we ran six machine made voices from the speech tool built into macOS through the voice check. All six were labelled human, with 93 to 99 percent confidence.

That means the voice check cannot tell a machine voice from a person. A clean voice result is not evidence of anything. It can only raise a flag when a recording is very flat, and it can flag a real person by mistake.

The check that carries weight is the spoken code. It is new for every session, so a pre recorded clip will not match it.

Do not give this check any weight in a decision. Replace it with a trained anti spoofing model and test it on recordings from many different speakers before that is ever considered.


## Photo match with an ID card or passport page

What it does
- This part is optional and the candidate must agree first. The candidate gives a photo of an ID card or a passport page and the camera frames from the face prompts are compared with the face in that photo.
- A face finder and a face recognition model run on our server. They turn each face into a list of numbers and measure how alike the two lists are. The best score over the camera frames is kept.
- The score is compared with one fixed line taken from the model maker. We did not tune it on our own data.
- Candidates only see that their photo check was received. They never see a score or a verdict. A person on the hiring team reads a short plain note.
- It is advice only. It never rejects anyone and never changes a score, a route or a weight. Only a result that says the faces looked different adds a note that suggests a short live check. Poor light, no face found and several faces are only notes.

What is kept
- The photos and the numbers made from them are used in memory and dropped straight away. No picture is saved and no face numbers are saved.
- Only a state word, the best score, the line it was compared with, the model name and the number of frames checked are kept, for 90 days.
- No cloud service is called.

Consent
- The session must be started with photo consent. Without it the photo route refuses the upload.
- The consent text says what is used, that it is compared on the server, that nothing is kept, that a person reads the result and that the comparison can be wrong.

Known limits
- Published tests of face recognition show different error rates across skin tones, ages and genders. We have not measured this on our own data, so we do not know how it behaves for the people who apply here.
- Poor light, glasses, head coverings, an old ID photo, a worn card, a scan with glare and a changed appearance can all make the same person look different.
- A look alike can pass. A photo or a screen held up to the camera can pass.
- The ID photo is often small and low quality, which makes errors more likely.
- A missing or unclear photo is never held against a candidate.

Testing needed before any weight is considered
1. Collect consented photo pairs from many volunteers across skin tones, ages, genders, head coverings, glasses and disabilities, using real cameras and real ID photos of different quality.
2. Measure the false reject rate and the false accept rate overall and for each group. Compare the groups. Drop or fix the check if the gaps are large.
3. Test against held up photos, screens and look alikes and report how many get through.
4. Set up an appeal path so that any candidate flagged by this check can ask a person to look again and can offer a live check as an equal alternative.
5. Have an outside reviewer check the results and the wording.
6. Until all of this is done the photo match stays advice only with no weight in any decision.
