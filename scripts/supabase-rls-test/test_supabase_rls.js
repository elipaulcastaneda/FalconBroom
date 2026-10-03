import { createClient } from '@supabase/supabase-js';
import dotenv from 'dotenv';

dotenv.config();

const SUPABASE_URL = process.env.SUPABASE_URL;
const SUPABASE_ANON_KEY = process.env.SUPABASE_ANON_KEY;
const TEST_EMAIL = process.env.TEST_USER_EMAIL;
const TEST_PASSWORD = process.env.TEST_USER_PASSWORD;

if (!SUPABASE_URL || !SUPABASE_ANON_KEY) {
  console.error('Set SUPABASE_URL and SUPABASE_ANON_KEY in environment');
  process.exit(1);
}

const supabase = createClient(SUPABASE_URL, SUPABASE_ANON_KEY);

async function run() {
  console.log('Signing up test user (or sign in if exists)');
  let { data: signUpData, error: signUpError } = await supabase.auth.signUp({
    email: TEST_EMAIL,
    password: TEST_PASSWORD,
  });
  if (signUpError && signUpError.status !== 400) {
    console.error('Signup error', signUpError);
    process.exit(1);
  }

  // Sign in
  const { data: signInData, error: signInError } = await supabase.auth.signInWithPassword({
    email: TEST_EMAIL,
    password: TEST_PASSWORD,
  });
  if (signInError) {
    console.error('Sign in error', signInError);
    process.exit(1);
  }

  const user = signInData.user;
  console.log('Signed in as', user?.id);

  console.log('Listing teams visible to user');
  const { data: teams, error: teamsError } = await supabase.from('teams').select('*');
  if (teamsError) {
    console.error('Teams select error', teamsError);
  } else {
    console.log('Teams:', teams);
  }

  console.log('Creating a team as the user');
  const { data: newTeam, error: newTeamError } = await supabase.from('teams').insert([{ name: 'CI Test Team' }]).select('*');
  if (newTeamError) {
    console.error('Insert team error', newTeamError);
  } else {
    console.log('Created team:', newTeam);
  }

  console.log('Done');
}

run().catch((err) => {
  console.error(err);
  process.exit(1);
});
